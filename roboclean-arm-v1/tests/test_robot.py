import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from roboclean_controle import Robot, LIMITE_F


def cycle(robot, tmax=600):
    robot.demarrer()
    fmax = 0.0
    while robot.st not in ("DONE", "STO") and robot.t < tmax:
        robot.step()
        fmax = max(fmax, robot.F)
    return fmax


class TestRoboClean(unittest.TestCase):
    def test_portee_initiale(self):
        for proto in (False, True):
            self.assertIsNotNone(Robot(proto).ik())

    def test_cycle_industriel(self):
        r = Robot(False)
        fmax = cycle(r)
        self.assertEqual(r.st, "DONE")
        self.assertLess(fmax, LIMITE_F)
        self.assertGreater(len(r.cells), 0)

    def test_cycle_prototype(self):
        r = Robot(True)
        fmax = cycle(r)
        self.assertEqual(r.st, "DONE")
        self.assertLess(fmax, 11.5)  # arrêt d'appui à 10 N

    def test_sto_surcharge(self):
        r = Robot(False)
        r.st, r.Fs = "PRESS", 20.0
        r.step()
        self.assertEqual(r.st, "STO")

    def test_arret_urgence_fige_le_robot(self):
        r = Robot(False)
        r.demarrer()
        r.arret("test")
        t = r.t
        r.step()
        self.assertEqual(r.t, t)

    def test_servos_dans_la_plage(self):
        s = Robot(False).servos()
        self.assertTrue(all(0 <= a <= 180 for a in s))


if __name__ == "__main__":
    unittest.main()
