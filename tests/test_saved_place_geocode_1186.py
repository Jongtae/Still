import unittest
from urllib.parse import parse_qs, urlparse

from personal_agent.local_tools import LocalTools


def place(name, admin1, admin2, lat, lon):
    return {'name': name, 'latitude': lat, 'longitude': lon, 'country': 'Testland', 'country_code': 'TL',
            'admin1': admin1, 'admin2': admin2}


def tools_with(known):
    """LocalTools served from a fake geocoder and forecast transport; no network."""
    tools, forecasts = LocalTools(), []

    def request(url, timeout, first=False):
        query = parse_qs(urlparse(url).query)
        if 'geocoding' in url:
            return {'results': known.get(query['name'][0], [])}
        forecasts.append((float(query['latitude'][0]), float(query['longitude'][0])))
        return {'timezone': 'UTC', 'current': {}}
    tools._public_request = request
    return tools, forecasts


class SavedPlaceGeocodeTests(unittest.TestCase):
    def test_saved_place_region_picks_the_matching_candidate_not_the_first_hit(self):
        far = place('Hilltop', 'Northland', 'Farcounty', 10.0, 10.0)
        near = place('Hilltop', 'Southland', 'Homecounty', 1.0, 1.0)
        tools, forecasts = tools_with({'Hilltop': [far, near]})
        result = tools.weather('Hilltop Southland Homecounty')
        self.assertEqual(forecasts, [(1.0, 1.0)])
        self.assertEqual(result['location']['admin1'], 'Southland')

    def test_result_names_the_place_used_and_the_text_searched(self):
        near = place('Hilltop', 'Southland', 'Homecounty', 1.0, 1.0)
        tools, _ = tools_with({'Hilltop': [near]})
        location = tools.weather('Hilltop')['location']
        self.assertEqual((location['name'], location['admin1'], location['admin2'], location['searched_text']),
                         ('Hilltop', 'Southland', 'Homecounty', 'Hilltop'))

    def test_without_region_words_ambiguity_is_still_reported(self):
        a, b = place('Hilltop', 'Northland', 'A', 10.0, 10.0), place('Hilltop', 'Southland', 'B', 1.0, 1.0)
        tools, _ = tools_with({'Hilltop': [a, b]})
        with self.assertRaises(ValueError):
            tools.weather('Hilltop')

    def test_observation_position_path_is_unchanged(self):
        tools, forecasts = tools_with({})
        result = tools.execute({'tool': 'weather', 'latitude': 3.5, 'longitude': 4.5})
        self.assertEqual(forecasts, [(3.5, 4.5)])
        self.assertNotIn('searched_text', result['location'])


if __name__ == '__main__':
    unittest.main()
