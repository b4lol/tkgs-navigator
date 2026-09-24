import unittest

from TKGSNavigator.core.constants import TKGS_TRANSPONDERS, TuningTarget
from TKGSNavigator.core.discovery import format_target, parse_target, plan_discovery
from TKGSNavigator.core.lamedb import Service, ServiceDatabase, Transponder


def database(transponders, orbital=420):
    tps, services = {}, []
    for index, (frequency, pol, rate, with_service) in enumerate(transponders):
        key = (0x01A40000, index + 1, 1)
        tps[key] = Transponder(key, frequency * 1000, rate * 1000, {"H": 0, "V": 1}[pol], orbital)
        if with_service:
            services.append(Service(index + 1, key, 1, "S%d" % index))
    return ServiceDatabase(tps, services)


class DiscoveryTests(unittest.TestCase):
    def test_learned_then_known_then_deep_each_once(self):
        db = database(
            [
                (11096, "H", 30000, True),
                (12380, "V", 27500, True),
                (12423, "H", 30000, True),
                (11054, "V", 30000, True),
                (11200, "V", 27500, False),
            ]
        )
        plan = plan_discovery(db, learned=TuningTarget(11054, "V", 30000))
        self.assertEqual(
            [(c.target.frequency, c.deep) for c in plan],
            [(11054, False), (12380, False), (12423, False), (11096, True)],
        )

    def test_learned_equal_to_a_known_transponder_is_tried_once(self):
        db = database([(12380, "V", 27500, True)])
        self.assertEqual(len(plan_discovery(db, learned=TKGS_TRANSPONDERS[0])), 1)

    def test_deep_search_is_capped_and_skips_other_satellites(self):
        db = database([(10700 + i * 10, "H", 27500, True) for i in range(60)])
        self.assertEqual(len(plan_discovery(db, deep_limit=5)), 5)
        self.assertEqual(plan_discovery(database([(12380, "V", 27500, True)], orbital=192)), [])

    def test_stored_target_round_trip(self):
        target = TuningTarget(12380, "V", 27500)
        self.assertEqual(parse_target(format_target(target)), target)
        for text in ("", "12380:X:27500", "a:V:b", "1:2"):
            self.assertIsNone(parse_target(text))


if __name__ == "__main__":
    unittest.main()
