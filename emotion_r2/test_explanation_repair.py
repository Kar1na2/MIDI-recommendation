import math
import unittest
from explanation_repair import family_features

class FamilyFeaturesTest(unittest.TestCase):
    def test_family_sum_and_zero_absence(self):
        result, unknown = family_features({'onset_density_total': 4, 'onset_density__acoustic_piano': 1,
                                           'onset_density__electric_piano': 2})
        self.assertEqual(result['family_keys'], 3)
        self.assertEqual(result['family_drums'], 0)
        self.assertFalse(unknown)

    def test_missing_quantization_is_not_zero(self):
        result, _ = family_features({})
        self.assertTrue(math.isnan(result['family_keys']))

    def test_unknown_instrument_is_reported(self):
        _, unknown = family_features({'onset_density_total': 1, 'onset_density__unknown_instrument': 1})
        self.assertEqual(unknown, {'unknown_instrument'})

if __name__ == '__main__': unittest.main()
