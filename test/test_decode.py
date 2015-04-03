#!/usr/bin/env python

"""Test the top level decoders with a single message each."""

import ais
import unittest
from . import test_data
import sys

class AisTopLevelDecoders(unittest.TestCase):

  def setUp(self):
    self.maxDiff = None

  def testAll(self):
    """Decode one of each top level message"""
    # TODO: message 20
    for entry in test_data.top_level:
      body = ''.join([line.split(',')[5] for line in entry['nmea']])
      pad = int(entry['nmea'][-1].split('*')[0][-1])
      msg = ais.decode(body, pad)
      expected = entry['result']
      if msg.keys() != expected.keys():
        sys.stderr.write('key mismatch: %s\n' % set(msg).symmetric_difference(set(expected)))
      self.assertDictEqual(msg, expected,
                           'Mismatch for id:%d\n%s\n%s' % (msg['id'] ,msg, expected))


class Ais6Decoders(unittest.TestCase):

  def testDecodeUnknownMessage6(self):
    # !AIVDM,1,1,,B,6B?n;be:cbapalgc;i6?Ow4,2*4A'
    # TODO(schwehr): Expose the C++ Python exception to Python.
    exception_happened = False
    try:
      ais.decode('6B?n;be:cbapalgc;i6?Ow4', 2)
    except Exception as error:
      exception_happened = True
      self.assertIn('6:669:11', str(error))
    self.assertTrue(exception_happened)

if __name__=='__main__':
  unittest.main()
