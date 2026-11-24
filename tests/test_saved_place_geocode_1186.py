import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from personal_agent.agent_runtime import Capabilities
from personal_agent.context_observations import ContextObservations
from personal_agent.current_context import PROFILE_OWNER, CurrentContext
from personal_agent.local_tools import LocalTools
from personal_agent.memory_service import MemoryService
from personal_agent.quickstart_store import QuickStore

from test_current_context import CHAT, GENERATION, NOON_SEOUL, POINT


def place(name, admin1, admin2, lat, lon):
    return {'name': name, 'latitude': lat, 'longitude': lon, 'country': 'Testland', 'country_code': 'TL',
            'admin1': admin1, 'admin2': admin2}


def tools_with(known, *, searches=None):
    """LocalTools served from a fake geocoder and forecast transport; no network."""
    tools, forecasts = LocalTools(), []

    def request(url, timeout, first=False):
        query = parse_qs(urlparse(url).query)
        if 'geocoding' in url:
            name = query['name'][0]
            if searches is not None:
                searches.append(name)
            return {'results': known.get(name, [])}
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


class SavedPlaceResolutionTests(unittest.TestCase):
    ref = 'profile:place.home'
    label = 'Hilltop Southland'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        self.memory = MemoryService(self.store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
        self.obs = ContextObservations(self.store, clock=lambda: NOON_SEOUL)
        self.context = CurrentContext(self.store, self.obs)
        self.job = self.store.enqueue('Weather for this place', 'weather')
        self.far = place('Hilltop', 'Northland', 'Farcounty', 10.0, 10.0)
        self.near = {**place('Hilltop', 'Southland', 'Homecounty', 1.23456, 2.34567),
                     'admin3': 'Hill district', 'admin4': 'Hill ward'}
        known = {'Hilltop': [self.far, self.near],
                 'Lakeside': [place('Lakeside', 'Lakeland', 'Lakecounty', 5.0, 6.0)]}
        self.searches = []
        network, self.forecasts = tools_with(known, searches=self.searches)
        self.caps = Capabilities(self.store, None, {}, '', self.job, lambda *a: None,
                                 network=network, current_context=self.context, lookup_sources=lambda: None)
        self.save(self.label)

    def save(self, label):
        self.memory.remember_profile(PROFILE_OWNER, 'settings', 'profile.place.home', label)

    def weather(self, ref=None):
        return self.caps.execute('weather', {'location_ref': ref or self.ref})

    def resolve(self):
        return self.context.resolve_location(self.job, self.ref)

    def test_first_lookup_keeps_region_match_and_second_uses_direct_forecast(self):
        revision = self.context.ref_revision(self.ref)
        self.assertEqual(self.resolve()['source']['resolved'], 'by_name')
        first = self.weather()
        self.assertEqual(first['sent'], {'city': self.label})
        self.assertEqual(first['location']['admin1'], 'Southland')
        self.assertEqual(first['location_source']['resolved'], 'by_name')
        self.assertEqual(self.searches, [self.label, 'Hilltop'])
        self.assertEqual(self.forecasts, [(1.23456, 2.34567)])

        kept = CurrentContext(QuickStore(self.store.root)).resolve_location(self.job, self.ref)
        self.assertEqual((kept['latitude'], kept['longitude'], kept['label']), (1.23, 2.35, self.label))
        self.assertEqual(kept['region'], {k: v for k, v in self.near.items() if k not in ('latitude', 'longitude')})
        self.assertEqual(kept['source']['kind'], 'profile_place_anchor')
        self.assertIn('stored geocode', kept['source']['basis'])
        self.assertEqual(self.context.ref_revision(self.ref), revision)

        second = self.weather()
        self.assertEqual(second['sent'], {'latitude': 1.23, 'longitude': 2.35})
        self.assertEqual(second['location']['name'], self.label)
        self.assertEqual(second['location']['region'], kept['region'])
        self.assertEqual(second['location_source']['resolved'], 'kept')
        self.assertEqual(self.forecasts, [(1.23456, 2.34567), (1.23, 2.35)])
        self.assertEqual(self.searches, [self.label, 'Hilltop'])

    def test_correction_requires_a_new_geocode_and_ignores_an_old_response(self):
        old_id = self.resolve()['memory_id']
        self.weather()
        revision = self.context.ref_revision(self.ref)
        self.save('Lakeside')
        self.context.keep_place_resolution(self.ref, old_id, self.far)
        self.assertNotEqual(self.context.ref_revision(self.ref), revision)
        self.assertEqual(self.resolve()['source']['resolved'], 'by_name')
        self.assertNotEqual(self.resolve()['memory_id'], old_id)
        self.assertEqual(self.weather()['location']['name'], 'Lakeside')
        self.assertEqual(self.searches, [self.label, 'Hilltop', 'Lakeside'])
        self.assertEqual((self.resolve()['latitude'], self.resolve()['longitude']), (5.0, 6.0))

    def test_deleting_memory_removes_its_geocode(self):
        memory_id = self.resolve()['memory_id']
        self.weather()
        self.memory.delete(PROFILE_OWNER, memory_id)
        with self.store.db() as db:
            self.assertIsNone(db.execute('SELECT memory_id FROM place_resolutions').fetchone())

    def test_ambiguous_or_missing_geocode_keeps_nothing(self):
        for label in ('Hilltop', 'Unknown'):
            with self.subTest(label=label):
                self.save(label)
                with self.assertRaises(ValueError):
                    self.weather()
                self.assertEqual(self.resolve()['source']['resolved'], 'by_name')
        self.assertEqual(self.searches, ['Hilltop', 'Unknown'])
        self.assertEqual(self.forecasts, [])

    def test_invalid_coordinates_and_mismatched_memory_id_keep_nothing(self):
        memory_id = self.resolve()['memory_id']
        self.context.keep_place_resolution(self.ref, 'other-revision', self.near)
        for latitude, longitude in ((True, 0), (float('nan'), 0), (0, float('inf')), (91, 0), (0, -181)):
            with self.subTest(latitude=latitude, longitude=longitude):
                self.context.keep_place_resolution(self.ref, memory_id, {'latitude': latitude, 'longitude': longitude})
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM place_resolutions').fetchone()[0], 0)
        self.assertEqual(self.resolve()['source']['resolved'], 'by_name')

    def test_observation_ref_still_uses_rounded_coordinates_and_its_own_source(self):
        self.store.put('telegram', {'enabled': True, 'user_id': CHAT, 'generation': GENERATION, 'cursor': 0})
        self.obs.set_controls({'enabled': True})
        message = {'from': {'id': CHAT}, 'chat': {'id': CHAT, 'type': 'private'}, 'date': int(NOON_SEOUL),
                   'message_id': 1, 'location': {**POINT, 'live_period': 3600}}
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.obs.ingest_telegram(db, {'update_id': 1, 'message': message}, GENERATION, CHAT)
            ref = 'obs:' + db.execute('SELECT id FROM context_observations').fetchone()['id']
        result = self.weather(ref)
        self.assertEqual(self.forecasts, [(37.57, 126.98)])
        self.assertEqual(self.searches, [])
        self.assertEqual(result['location_source']['kind'], 'live_position_report')
        self.assertNotIn('resolved', result['location_source'])
        self.assertNotIn('region', result['location'])
        self.assertEqual(self.resolve()['source']['resolved'], 'by_name')


if __name__ == '__main__':
    unittest.main()
