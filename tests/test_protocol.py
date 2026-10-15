"""python -m unittest discover tests"""

import unittest

from pc.core.workers import build_points, parse_positions
from pc.devices import protocol
from pc.gui.widgets import parse_si


class ProtocolTest(unittest.TestCase):

    def test_roundtrip(self):
        line = protocol.encode('MOVE 1 12.500').decode()
        self.assertTrue(line.endswith('\n'))
        self.assertEqual(protocol.decode(line), 'MOVE 1 12.500')

    def test_checksum_matches_firmware(self):
        # XOR aller Zeichen von "HB": 'H' ^ 'B' = 0x48 ^ 0x42 = 0x0A
        self.assertEqual(protocol.encode('HB'), b'HB*0A\n')
        self.assertEqual(protocol.decode('HB*0a'), 'HB')

    def test_bad_checksum(self):
        self.assertIsNone(protocol.decode('HB*0B'))
        self.assertIsNone(protocol.decode('HB'))
        self.assertIsNone(protocol.decode('HB*0'))

    def test_float_has_no_exponent(self):
        self.assertEqual(protocol.fmt_float(1e-5), '0.000')
        self.assertEqual(protocol.fmt_float(-0.0001), '0.000')
        self.assertEqual(protocol.fmt_float(-12.3456), '-12.346')

    def test_parse_lines(self):
        s = protocol.parse_summary('S 3 10 1 0 1 0 0 1')
        self.assertEqual((s.state, s.err, s.err_axis, s.key), (3, 10, 1, True))
        axis, tel, alive, link = protocol.parse_master_axis('A 1 1 12.50 15.00 0 0 2 1 1')
        self.assertEqual((axis, tel.busy, tel.pos, tel.target, alive, link),
                         (1, True, 12.5, 15.0, True, True))
        m = protocol.parse_module('M 2 0 0 -3.25 -3.25 0 0')
        self.assertEqual((m.state, m.pos), (2, -3.25))
        self.assertTrue(protocol.is_reply('P gear0 5.000'))
        self.assertFalse(protocol.is_reply('M 2 0 0 0 0 0 0'))


class PositionsTest(unittest.TestCase):

    def test_range(self):
        self.assertEqual(parse_positions('0:10:5'), [0, 5, 10])
        self.assertEqual(parse_positions('10:0:5'), [10, 5, 0])
        self.assertEqual(parse_positions('0:1:0,25'), [0, 0.25, 0.5, 0.75, 1.0])

    def test_list(self):
        self.assertEqual(parse_positions('0; 10 22,5'), [0, 10, 22.5])
        self.assertEqual(parse_positions(''), [])

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_positions('0:10')
        with self.assertRaises(ValueError):
            parse_positions('0:10:0')

    def test_grid(self):
        self.assertEqual(build_points({}), [{}])
        pts = build_points({1: [0, 1], 0: [5, 6], 2: []})
        self.assertEqual(pts, [{0: 5, 1: 0}, {0: 5, 1: 1}, {0: 6, 1: 0}, {0: 6, 1: 1}])


class SITest(unittest.TestCase):

    def test_parse(self):
        self.assertAlmostEqual(parse_si('200 us'), 2e-4)
        self.assertAlmostEqual(parse_si('2e-4'), 2e-4)
        self.assertAlmostEqual(parse_si('1,5 V'), 1.5)
        self.assertAlmostEqual(parse_si('-500 mV'), -0.5)


if __name__ == '__main__':
    unittest.main()
