#!/usr/bin/env python
from __future__ import print_function

# Since 2010-May-06
# Yet another rewrite of my code to put ais data from a nais feed into postgis

# First just try to rip a whole day's log.  How long will it take?

# Skip normalization first...

import ais
from ais import decode # Make sure we have the right ais module

import sys
import re

import psycopg2
#import psycopg2.extras


def print24(msg):
    if msg['part_num'] == 0:
        #print (msg)
        print ('24_A: name = ',msg['name'].rstrip(' @'),'\tmmsi = ',msg['mmsi'])
        return
    if msg['part_num'] == 1:
        print ('24_B: tac  = %3d' % msg['type_and_cargo'],'callsign=',msg['callsign'].rstrip(' @'))

def check_ref_counts(a_dict):
    print ('Checking ref counts on', a_dict)
    print ('\tmsg:', sys.getrefcount(a_dict))
    for d in a_dict:
        print ('\t',d, sys.getrefcount(d))

# Making this regex more like how I like!  lower_lower
uscg_ais_nmea_regex_str = r'''^!(?P<talker>AI)(?P<string_type>VD[MO])
,(?P<total>\d?)
,(?P<sen_num>\d?)
,(?P<seq_id>[0-9]?)
,(?P<chan>[AB])
,(?P<body>[;:=@a-zA-Z0-9<>\?\'\`]*)
,(?P<fill_bits>\d)\*(?P<checksum>[0-9A-F][0-9A-F])
(  
  (,S(?P<slot>\d*))
  | (,s(?P<s_rssi>\d*))
  | (,d(?P<signal_strength>[-0-9]*))
  | (,t(?P<t_recver_hhmmss>(?P<t_hour>\d\d)(?P<t_min>\d\d)(?P<t_sec>\d\d.\d*)))
  | (,T(?P<time_of_arrival>[^,]*))
  | (,x(?P<x_station_counter>[0-9]*))
  | (,(?P<station>(?P<station_type>[rbB])[a-zA-Z0-9_]*))
)*
,(?P<time_stamp>\d+([.]\d+)?)?
'''
uscg_ais_nmea_regex = re.compile(uscg_ais_nmea_regex_str,  re.VERBOSE)


import Queue

class LineQueue(Queue.Queue):
    'Break input into input lines'
    def __init__(self, separator='\n', maxsize=0, verbose=False):
        Queue.Queue.__init__(self,maxsize)
        self.input_buf = ''
        self.v = verbose
        self.separator = separator

    def put(self, text):
        self.input_buf += text
        groups = self.input_buf.split(self.separator)
        if len(groups) == 1:
            self.input_buf = groups[0]
            # no complete groups
            return
        elif len(groups[-1]) == 0:
            # last group was properly terminated
            for g in groups[:-1]:
                Queue.Queue.put(self,g)
            self.input_buf = ''
        else:
            # last part is a fragment
            for g in groups[:-1]:
                Queue.Queue.put(self,g)
            self.input_buf = groups[-1]

class NormQueue(Queue.Queue):
    '''Normalized AIS messages that are multiple lines

    - FIX: Can I get away with only keeping the body of beginning parts?

    - works based USCG dict representation of a line that comes back from the regex.
    - not worrying about the checksum.  Assume it already has been validated
    - 160 stations in the US with 10 seq channels... should not be too much data
    - assumes each station will send in order messages without duplicates
    '''
    def __init__(self, separator='\n', maxsize=0, verbose=False):
        Queue.Queue.__init__(self,maxsize)
        self.input_buf = ''
        self.v = verbose
        self.separator = separator
        self.stations = {}

    def put(self, msg):
        if not isinstance(msg, dict): raise TypeError('Message must be a dictionary')

        total = int(msg['total'])
        station = msg['station']
        if station not in self.stations:
            self.stations[station] = {0:[ ],1:[ ],2:[ ],3:[ ],4:[ ],
                                      5:[ ],6:[ ],7:[ ],8:[ ],9:[ ]}

        if total == 1:
            Queue.Queue.put(self,msg) # EASY case
            return

        seq = int(msg['seq_id'])
        sen_num = int(msg['sen_num'])

        if sen_num == 1:
            # Flush that station's seq and start it with a new msg component
            self.stations[station][seq] = [msg['body'],] # START
            return

        if sen_num != len(self.stations[station][seq]) + 1:
            self.stations[station][seq] = [ ] # DROP and flush... bad seq
            return
        
        if sen_num == total:
            msgs = self.stations[station][seq]
            self.stations[station][seq] = [ ] # FLUSH
            if len(msgs) != total - 1:
                return # INCOMPLETE was missing part - so just drop it

            # all parts should have the same metadata, but last has the fill bits
            msg['body'] = ''.join(msgs) + msg['body']
            msg['total'] = msg['seq_num'] = 1
            Queue.Queue.put(self,msg)
            return
        
        self.stations[station][seq].append(msg['body']) # not first, not last



def insert_or_update(cx, cu, sql_insert, sql_update, values):
    'If the value might be new, try to insert first'
    #print 'insert_or_update:',values
    #print 'insert_or_update:',values['mmsi'],values['name'],values['type_and_cargo']
    success = True
    try:
        cu.execute(sql_insert,values)
    except psycopg2.IntegrityError:
        # psycopg2.IntegrityError: duplicate key value violates unique constraint
        print ('insert failed')
        success = False

    cx.commit()
    if success: return

    print (cu.mogrify(sql_update, values))
    cu.execute(sql_update, values)
    cx.commit()


sql_vessel_name_create='''CREATE TABLE vessel_name (
       mmsi INT PRIMARY KEY,
       name VARCHAR(25), -- only need 20
       type_and_cargo INT
);'''

class VesselNames:
    'Local caching of vessel names for more speed and less DB load'
    sql_insert = 'INSERT INTO vessel_name VALUES (%(mmsi)s,%(name)s,%(type_and_cargo)s);'
    sql_update = 'UPDATE vessel_name SET name = %(name)s, type_and_cargo=%(type_and_cargo)s WHERE mmsi = %(mmsi)s;'

    sql_update_tac  = 'UPDATE vessel_name SET type_and_cargo=%(type_and_cargo)s WHERE mmsi = %(mmsi)s;'
    sql_update_name = 'UPDATE vessel_name SET name = %(name)s WHERE mmsi = %(mmsi)s;'

    def __init__(self, db_cx, verbose=False):
        self.cx = db_cx
        self.cu = self.cx.cursor()
        self.vessels = {}
        self.v = verbose
        # Could do a create and commit here of the table

    def update_partial(self, vessel_dict):
        # Only do the name or type_and_cargo... msg 24 class B
        mmsi = vessel_dict['mmsi']
        if 'name' in vessel_dict:
            name = vessel_dict['name'].rstrip(' @')
            type_and_cargo = None
        else:
            assert (msg['part_num']==1)
            name = None
            type_and_cargo = msg['type_and_cargo']

        if name is not None and len(name)==0:
            if self.v: print ('EMPTY NAME:',mmsi)
            return

        if mmsi < 200000000:
            if name is not None:
                #print ('USELESS: mmsi =',mmsi, 'name:',name)
                pass
            else:
                #print ('USELESS: mmsi =',mmsi, 'name:',name, 'type_and_cargo:',type_and_cargo)
                pass
            return # Drop these... useless

        new_vessel = {'mmsi':mmsi,'name':name,'type_and_cargo':type_and_cargo}

        # Totally new ship to us
        if mmsi not in self.vessels:
            if name is None:
                # FIX: add logic to hold on to the type_and_cargo for those we do not have names for
                #self.vessels[mmsi] = (None, type_and_cargo)
                # Don't update db until we have a name
                return

            # Have a name - try to insert - FIX: make it insert or update?
            #print ('before',mmsi)
            try:
                self.cu.execute(self.sql_insert,new_vessel)
                if self.v: print ('ADDED:',name, mmsi)
            except psycopg2.IntegrityError, e:
                print (mmsi,'already in the database', str(e))
                pass # Already in the database
            #print ('after',mmsi)
            self.cx.commit()
            self.vessels[mmsi] = (name, type_and_cargo)
            return

        old_name,old_type_and_cargo = self.vessels[mmsi]

        if name is None:
            if type_and_cargo == old_type_and_cargo or old_name == None:
                #print ('NO_CHANGE_TO_CARGO:',mmsi)
                return
            
            #print ('UPDATING_B:',mmsi,'  ',old_name,old_type_and_cargo,'->',old_name,type_and_cargo)
            self.cu.execute(self.sql_update_tac, new_vessel)
            self.cx.commit()
            #print ('\ttac: ',old_name, type_and_cargo)
            self.vessels[mmsi] = (old_name, type_and_cargo)
            return

        # Update the name
        if name == old_name:
            #print ('NO_CHANGE_TO_NAME:',mmsi,old_name)
            return

        if self.v: print ('UPDATING_B:',mmsi,'  ',old_name,'->',name)
        # FIX: what if someone deletes the entry from the db?

        self.cu.execute(self.sql_update_name, new_vessel)
        self.cx.commit()
        #print ('\tname: ',name, old_type_and_cargo)
        self.vessels[mmsi] = (name, old_type_and_cargo)
        

    def update(self, vessel_dict):
        # vessel_dict must have mmsi, name, type_and_cargo
        mmsi = vessel_dict['mmsi']
        #if mmsi in (0,1,1193046):
        if mmsi < 200000000:
            # These vessels should be reported to the USCG
            if self.v: print ('USELESS: mmsi =',mmsi, 'name:',vessel_dict['name'])
            return # Drop these... useless

        name = vessel_dict['name'];
        type_and_cargo = vessel_dict['type_and_cargo']
        
        if mmsi not in self.vessels:
            #print ('NEW: mmsi =',mmsi)
            insert_or_update(self.cx, self.cu, self.sql_insert, self.sql_update, vessel_dict)
            self.vessels[mmsi] = (name, type_and_cargo)
            return

        #if mmsi in self.vessels:
        old_name,old_type_and_cargo = self.vessels[mmsi]
        if old_name == name and old_type_and_cargo == type_and_cargo:
            #print ('NO_CHANGE:',mmsi)
            return

        # Know we have inserted it, so safe to update
        #if mmsi == 367178330:
        #print ('UPDATING:',mmsi,'  ',old_name,old_type_and_cargo,'->',name,type_and_cargo)
        if old_name != name:
            print ('UPDATING:',mmsi,'  "%s" -> "%s"' % (old_name,name))
        #print (old_name,old_type_and_cargo,'->',)
        #print (name,type_and_cargo)

        # FIX: what if someone deletes the entry from the db?
        self.cu.execute(self.sql_update, vessel_dict)
        self.cx.commit()
        self.vessels[mmsi] = (name, type_and_cargo)

        
if __name__ == '__main__':

    match_count = 0
    counters = {}
    for i in range(30):
        counters[i] = 0

    cx = psycopg2.connect("dbname='testing'")

    if True:
        for i in range(5): print ('   *** WARNING ***  - Removing entries from vessel_name for testing')
        print ()
        cu = cx.cursor()
        cu.execute('DELETE FROM vessel_name;')
    else:
        # FIX: load from the database
        pass
    

    vessel_names = VesselNames(cx)

    line_queue = LineQueue()
    norm_queue = NormQueue()


    #for line_num, text in enumerate(open('1e6.multi')):
    #for line_num, line in enumerate(file('/nobackup/dl1/20100505.norm')):
    for line_num, text in enumerate(open('/Users/schwehr/Desktop/DeepwaterHorizon/ais-dl1/uscg-nais-dl1-2010-04-28.norm.dedup')):
    #for line_num, text in enumerate(open('test.aivdm')):
    #for line_num, text in enumerate(open('trouble.ais')):

        #if line_num > 300: break
        #print ()
        if line_num % 100000 == 0: print ("line: %d   %d" % (line_num,match_count) )
        if 'AIVDM' not in text:
            continue

        line_queue.put(text)

        while line_queue.qsize() > 0:
            line = line_queue.get(False)
            if len(line) < 15 or '!' != line[0]: continue # Try to go faster

            #print ('line:',line)
            try:
                match = uscg_ais_nmea_regex.search(line).groupdict()
                match_count += 1
            except AttributeError:
                if 'AIVDM' in line:
                    print ('BAD_MATCH:',line)
                continue

            norm_queue.put(match)

            while norm_queue.qsize()>0:
       
                try:
                    result = norm_queue.get(False)
                except Queue.Empty:
                    continue

                if len(result['body']) < 10: continue
                # FIX: make sure we have all the critical messages

                # FIX: add 9
                if result['body'][0] not in ('1', '2', '3', '5', 'B', 'C', 'H') :
                    #print( 'skipping',result['body'])
                    continue
                #print( 'not skipping',result['body'])
                
                try:
                     msg = ais.decode(result['body'])
                #except ais.decode.error:
                except Exception as e:
                    #print ()
                    
                    if 'not yet handled' in str(e):
                        continue
                    if ' not known' in str(e): continue

                    print ('BAD Decode:',result['body'][0]) #result
                        #continue
                    print ('E:',Exception)
                    print ('e:',e)
                    continue
                    #pass # Bad or unhandled message

                #continue #  FIX: Remove after debugging

                counters[msg['id']] += 1

                if False:
                    print ('id:',msg['id'], counters[msg['id']])

                    print ('msg', sys.getrefcount(msg), sys.getrefcount(msg['id']))

                    if msg['id'] == 19:
                        print (' *** x:',sys.getrefcount(msg['x']), sys.getrefcount(msg['dim_c']))
                    check_ref_counts(msg)

                if msg['id'] == 24:
                    #print24(msg)
                    vessel_names.update_partial(msg)
                    
                #continue # FIX remove

                if msg['id'] in (5,19):
                    msg['name'] = msg['name'].strip(' @')
                    if len(msg['name']) == 0: continue # Skip blank names
                    #print ('UPDATING vessel name', msg)
                    #vessel_names.update(msg['mmsi'], msg['name'].rstrip('@'), msg['type_and_cargo'])
                    #if msg['mmsi'] == 367178330:
                    #    print (' CHECK:', msg['mmsi'], msg['name'])
                    vessel_names.update(msg)
                    #print ()
                        
    print ('match_count:',match_count)
    #print (counters)
    for key in counters:
        if counters[key] < 1: continue
        print ('%d: %d' % (key,counters[key]))