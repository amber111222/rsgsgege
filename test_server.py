import io
import json
import unittest
from unittest.mock import patch
import server


class CatalogueTests(unittest.TestCase):
    def test_artist_is_catalogue_artist_not_uploader_or_title_prefix(self):
        row=server.track_record({'id':123,'title':'Jenny - Live','artist':{'id':17,'name':'Steve Aoki'},'album':{},'duration':210})
        self.assertEqual(row['user']['id'],'dz-17')
        self.assertEqual(row['user']['name'],'Steve Aoki')
        self.assertEqual(row['source'],'deezer')
        self.assertFalse(row['user']['is_verified'])

    def test_artist_filter_rejects_namesake_and_preserves_pagination(self):
        artist={'id':4170146,'name':'Баста'}
        page={'data':[{'id':10,'title':'Сансара','artist':artist}, {'id':11,'title':'Other','artist':{'id':5,'name':'Баста'}},{'id':12,'title':'Моя игра','artist':artist}]}
        with patch.object(server,'deezer',return_value=page):
            result=server.artist_catalogue('4170146',1,0)
        self.assertEqual([x['id'] for x in result['data']],['dz-10'])
        self.assertTrue(result['has_more'])

    def test_ids_are_numeric_catalogue_ids(self):
        for value in ['../secret','https://example.com','dz-1/stream','1?key=x','-1']:
            with self.assertRaises(ValueError):server.number(value)

    def test_health_has_no_upstream_dependency(self):
        self.assertEqual(server.route('/health',{})['status'],'ok')

    def test_catalogue_omits_explicit_interviews_but_does_not_guess_from_artist_names(self):
        artist = {'id':4170146, 'name':'Баста'}
        self.assertIsNone(server.track_record({'id':1, 'title':'Интервью с артистом', 'artist':artist}))
        self.assertIsNone(server.track_record({'id':2, 'title':'Music Podcast 01', 'artist':artist}))
        # The source labels this a release. Its words alone do not prove it is an interview.
        self.assertIsNotNone(server.track_record({'id':1711271137, 'title':'Моргенштерн Скриптонит Легенда', 'artist':artist}))


class VideoMatchingTests(unittest.TestCase):
    def setUp(self):
        self.track = {'id':2248173007, 'artist':{'name':'Баста'}, 'title':'Где ты теперь и с кем', 'duration':301}

    def entry(self, video_id='LQ_be1KSonc', **changes):
        return dict({'id':video_id, 'title':'Баста, HammAli & Navai - Где ты теперь и с кем',
                     'channel':'HammAli & Navai', 'duration':306}, **changes)

    def test_accepts_actual_basta_collaboration_with_artist_in_title(self):
        result = server.video_result(self.track, [self.entry()])
        self.assertEqual(result['videoId'], 'LQ_be1KSonc')
        self.assertEqual(result['candidates'][0]['duration'], 306)
        self.assertEqual(result['match'], 'metadata')

    def test_rejects_reported_interview_instead_of_playing_closest_search_hit(self):
        reported = {'artist':{'name':'Баста'}, 'title':'Моргенштерн Скриптонит Легенда', 'duration':85}
        interview = self.entry(title='МОРГЕНШТЕРН О СКРИПТОНИТЕ / ИНТЕРВЬЮ МОРГЕНШТЕРНА', channel='AboutStar', duration=102)
        with self.assertRaisesRegex(LookupError, 'надёжно'):
            server.video_result(reported, [interview])
        # Still reject an interview that contains all the requested song/artist words.
        self.assertEqual(server.video_candidates(self.track, [self.entry(title='Баста - Где ты теперь и с кем интервью', duration=301)]), [])

    def test_requires_song_artist_and_duration_not_a_popular_channel(self):
        for bad in [self.entry(title='Баста - Сансара'),
                    self.entry(title='Другой артист - Где ты теперь и с кем'),
                    self.entry(duration=1100), self.entry(duration=None),
                    self.entry(title='Баста - Где ты теперь и с кем (karaoke)')]:
            self.assertEqual(server.video_candidates(self.track, [bad]), [])

    def test_latin_transliteration_and_html_entities_are_comparable(self):
        self.assertEqual(len(server.video_candidates(self.track, [self.entry(title='Basta, HammAli &amp; Navai - Gde ty teper i s kem')])), 1)

    def test_topic_song_title_without_artist_is_valid_only_for_artist_channel(self):
        good = self.entry(title='Где ты теперь и с кем', channel='Баста - Topic', duration=301)
        bad = self.entry(title='Где ты теперь и с кем', channel='Random - Topic', duration=301)
        self.assertEqual(len(server.video_candidates(self.track, [good])), 1)
        self.assertEqual(server.video_candidates(self.track, [bad]), [])

    def test_candidates_are_deduplicated_ranked_and_bounded(self):
        ordinary = self.entry('abcdefghijk', duration=310)
        topic = self.entry('12345678901', title='Где ты теперь и с кем', channel='Basta - Topic', duration=301)
        third = self.entry('ABCDEFGHIJK', duration=312)
        fourth = self.entry('09876543210', duration=313)
        candidates = server.video_candidates(self.track, [ordinary, ordinary, fourth, third, topic])
        self.assertEqual([v['videoId'] for v in candidates], ['12345678901', 'abcdefghijk', 'ABCDEFGHIJK'])

    def test_live_private_and_unrelated_remix_are_not_fallbacks(self):
        for bad in [self.entry(is_live=True), self.entry(availability='private'),
                    self.entry(title=self.entry()['title']+' live'),
                    self.entry(title=self.entry()['title']+' remix')]:
            self.assertEqual(server.video_candidates(self.track, [bad]), [])

    def test_iso_duration(self):
        self.assertEqual(server.iso_duration('PT5M6S'), 306)
        self.assertEqual(server.iso_duration('PT1H2M'), 3720)
        self.assertEqual(server.iso_duration('invalid'), 0)

    def test_official_api_checks_details_instead_of_accepting_first_search_result(self):
        search = {'items':[{'id':{'videoId':'abcdefghijk'}}, {'id':{'videoId':'LQ_be1KSonc'}}]}
        details = {'items':[
            {'id':'abcdefghijk', 'snippet':{'title':'Баста - Где ты теперь и с кем интервью', 'channelTitle':'Other'},
             'contentDetails':{'duration':'PT5M1S'}, 'status':{'embeddable':True, 'privacyStatus':'public'}},
            {'id':'LQ_be1KSonc', 'snippet':{'title':self.entry()['title'], 'channelTitle':'HammAli & Navai'},
             'contentDetails':{'duration':'PT5M6S'}, 'status':{'embeddable':True, 'privacyStatus':'public'}}]}
        responses = [io.BytesIO(json.dumps(result).encode()) for result in (search, details)]
        with patch.dict(server.os.environ, {'YOUTUBE_API_KEY':'test-key'}), \
             patch.object(server, 'deezer', return_value=self.track), \
             patch.object(server.urllib.request, 'urlopen', side_effect=responses) as upstream:
            result = server.resolve_video('dz-999991')
        self.assertEqual(result['videoId'], 'LQ_be1KSonc')
        self.assertEqual(len(result['candidates']), 1)
        self.assertIn('/videos?', upstream.call_args.args[0])


if __name__=='__main__':unittest.main()
