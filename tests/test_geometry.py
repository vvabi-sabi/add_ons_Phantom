import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "phantom_storyboard" / "geometry.py"
SPEC = importlib.util.spec_from_file_location("phantom_geometry", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load geometry module")
geometry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(geometry)


class CurvedScreenGeometryTests(unittest.TestCase):
    def test_held_frames_cover_range_once(self):
        groups = [(sample, list(frames)) for sample, frames in geometry.held_frame_groups(1, 6, 2)]
        self.assertEqual(groups, [(1, [1, 2]), (3, [3, 4]), (5, [5, 6])])

    def test_partial_final_hold(self):
        groups = [(sample, list(frames)) for sample, frames in geometry.held_frame_groups(3, 7, 3)]
        self.assertEqual(groups, [(3, [3, 4, 5]), (6, [6, 7])])

    def test_screen_has_expected_topology_and_uvs(self):
        vertices, faces, uvs = geometry.curved_screen_geometry(12, 7, 2, 4)
        self.assertEqual(len(vertices), 10)
        self.assertEqual(len(faces), 4)
        self.assertEqual(len(uvs), len(vertices))
        self.assertEqual(vertices[4][1], 0.0)
        self.assertEqual(vertices[0][1], -2.0)
        self.assertEqual(uvs[0], (0.0, 0.0))
        self.assertEqual(uvs[-1], (1.0, 1.0))

    def test_screen_rejects_invalid_dimensions(self):
        with self.assertRaises(ValueError):
            geometry.curved_screen_geometry(0, 7, 2, 4)
        with self.assertRaises(ValueError):
            geometry.curved_screen_geometry(12, 7, 2, 1)

    def test_cube_is_complete(self):
        vertices, faces = geometry.unit_cube_geometry()
        self.assertEqual((len(vertices), len(faces)), (8, 6))


if __name__ == "__main__":
    unittest.main()
