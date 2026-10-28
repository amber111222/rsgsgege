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


if __name__=='__main__':unittest.main()
