"""Smoke tests for alpha validators; generated code is never executed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from behavior_validator import check_python
from unity_behavior_validator import clean_source, door_check, flashlight_check

DOOR = """using UnityEngine;
public class DoorToggle : MonoBehaviour {
  public Transform door;
  private Quaternion initialRotation;
  void Start() { initialRotation = door.localRotation; }
  public void ToggleDoor() {
    door.localRotation = door.localRotation == initialRotation ?
      Quaternion.Euler(0, 90, 0) : initialRotation;
  }
}
"""
FLASHLIGHT = """using UnityEngine;
public class FlashlightToggle : MonoBehaviour {
 public Light flashlight;
 void Update() { if (Input.GetKeyDown(KeyCode.F)) {
   flashlight.enabled = !flashlight.enabled;
 } }
}
"""

class BehaviorValidatorTests(unittest.TestCase):
    def test_python_clamp(self):
        code = "def clamp_number(value, minimum, maximum):\n return max(minimum, min(value, maximum))\n"
        result = check_python(code, "python_clamp")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["passed"], 8)

    def test_python_even(self):
        code = "def count_even(numbers):\n return sum(1 for number in numbers if number % 2 == 0)\n"
        result = check_python(code, "python_even")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["passed"], 6)

    def test_unsupported_python_inconclusive(self):
        code = "def count_even(numbers):\n return __import__('os').getpid()\n"
        self.assertEqual(check_python(code, "python_even")["status"], "INCONCLUSIVE")

    def test_door_absolute_rotation_is_requirement_failure(self):
        self.assertEqual(door_check(clean_source(DOOR))["status"], "REQUIREMENT_FAIL")

    def test_door_relative_rotation_is_recognized(self):
        code = DOOR.replace(
            "Quaternion.Euler(0, 90, 0) : initialRotation",
            "initialRotation * Quaternion.Euler(0, 90, 0) : initialRotation",
        )
        self.assertEqual(door_check(clean_source(code))["status"], "SUPPORTED_PATTERN_PASS")

    def test_flashlight_pattern_is_recognized(self):
        self.assertEqual(flashlight_check(clean_source(FLASHLIGHT))["status"], "SUPPORTED_PATTERN_PASS")

if __name__ == "__main__":
    unittest.main()
