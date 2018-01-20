from mongo import mongoBasedResource
from flask_restful import reqparse
import gridfs
import json
import pycountry

import hashlib

parser = reqparse.RequestParser()

# Add Arguments (params) to parser here ...
parser.add_argument('taxon', type=str, help='Taxonomic name to search occurrences for')
parser.add_argument('locality', type=str, help='Locality name to filter taxonomic occurrences by')
parser.add_argument('period', type=str, help='The geologic time period to filter taxonomic occurrences by')
parser.add_argument('institution_code', type=str, help='The abbreviated code submitted by data provider to filter taxonomic occurrences by')

#
#
#
class es_occurrences(mongoBasedResource):
	def process(self):

		# returns dictionary of params as defined in endpoint description
		# will throw exception if required param is not present
		params = self.getParams()
		# offset and limit returned as ints with default if not set
		offset = self.offset()
		limit = self.limit()

		# iDigBio uses strange field names
		idigbioReplacements = {'scientificname': 'dwc:scientificName', 'dwc:specificEpithet': 'species', 'genus': 'dwc:genus', 'family': 'dwc:family', 'order': 'dwc:order', 'class': 'dwc:class', 'phylum': 'dwc:phylum', 'kingdom': 'dwc:kingdom',
		'locality': 'dwc:locality', 'county': 'dwc:county', 'state': 'dwc:stateProvince', 'country': 'dwc:country'}

		# There are a few PBDB edge cases too
		pbdbReplacements = {'scientificname': 'accepted_name', 'country': 'cc1'}

		if self.paramCount > 0:
			res = None
			idbQuery = {
				"size": limit,
				"query":{
					"bool":{
						"must":[]
					}
				},
				"sort": [
					"_score",
					"_doc"
				]
			}
			pbdbQuery = {
				"size": limit,
				"query":{
					"bool":{
						"must":[]
					}
				},
				"sort": [
					"_score",
					"_doc"
				]
			}
			if params['searchFrom']:
				query['search_after'] = json.loads(params['searchFrom'])
			# Parse the search term parameter and get the initial result from ES
			if not params['terms']:
				return {"ERROR": "You must provide at least one field:term pair"}
			searchTerms = params['terms'].split('|')
			for search in searchTerms:
				pbdbAdded = False
				idbAdded = False
				field, term = search.split(':')
				if field in idigbioReplacements:
					idbQuery['query']['bool']['must'].append({"match": {idigbioReplacements[field]: term}})
					idbAdded = True
				if field in pbdbReplacements:
					if field == 'country':
						countryTerm = term[0].upper() + term[1:]
						country = pycountry.countries.get(name=countryTerm)
						countryCode = country.alpha_2.lower()
						term = countryCode
					pbdbQuery['query']['bool']['must'].append({"match": {pbdbReplacements[field]: term}})
					pbdbAdded = True
				if field == 'geopoint' or field == 'paleogeopoint':
					if field == 'paleogeopoint':
						field = 'paleoGeoPoint'
					else:
						field = 'geoPoint'
					# Check to see if a radius was set, else use default (10km)
					matchRadius = '10km'
					if params['geoPointRadius']:
						matchRadius = params['geoPointRadius']
					pbdbQuery['query']['bool']['filter'] = {'geo_distance': {'distance': matchRadius, field: term}}
					idbQuery['query']['bool']['filter'] = {'geo_distance': {'distance': matchRadius, field: term}}
					idbAdded = pbdbAdded = True
				if idbAdded is False:
					idbQuery['query']['bool']['must'].append({"match": {field: term}})
				if pbdbAdded is False:
					pbdbQuery['query']['bool']['must'].append({"match": {field: term}})

			#	query['query']['bool']['must'].append({"match": {field: term}})
			if len(idbQuery['query']['bool']['must']) == 0:
				idbQuery['query']['bool']['must'] = {'match_all': {}}
			if len(pbdbQuery['query']['bool']['must']) == 0:
				pbdbQuery['query']['bool']['must'] = {'match_all': {}}
			pbdbRes = self.es.search(index="pbdb", body=pbdbQuery)
			idbRes = self.es.search(index="idigbio", body=idbQuery)
			# Get the fields to match on
			matches = {'results': {}}
			localityMatch = {'idigbio': 'dwc:county', 'pbdb': 'county'}
			if params['localityMatchLevel']:
				localityMatch['pbdb'] = params['localityMatchLevel']
				localityMatch['idigbio'] = 'dwc:' + params['localityMatchLevel']
				if params['localityMatchLevel'] == 'state':
					localityMatch['idigbio'] = localityMatch['idigbio'] + 'Province'

			taxonMatch = {'idigbio': 'dwc:genus', 'pbdb': 'genus'}
			if params['taxonMatchLevel']:
				localityMatch['pbdb'] = params['taxonMatchLevel']
				localityMatch['idigbio'] = 'dwc:' + params['taxonMatchLevel']

			# PBDB uses numeric min/max ma so we don't have to worry about that here
			chronoMatch = {'idigbio': {'early': 'dwc:earliestPeriodOrLowestSystem', 'late': 'dwc:latestPeriodOrHighestSystem'}}
			if params['chronoMatchLevel']:
				if params['chronoMatchLevel'] == 'stage':
					chronoMatch = {'idigbio': {'early': 'dwc:earliestAgeOrLowestStage', 'late': 'dwc:latestAgeOrHighestStage'}}
				elif params['chronoMatchLevel'] == 'series':
					chronoMatch = {'idigbio': {'early': 'dwc:earliestEpochOrLowestSeries', 'late': 'dwc:latestEpochOrHighestSeries'}}
				elif params['chronoMatchLevel'] == 'erathem':
					chronoMatch = {'idigbio': {'early': 'dwc:earliestEraOrLowestErathem', 'late': 'dwc:latestEraOrHighestErathem'}}
			for res in [pbdbRes, idbRes]:
				if res:
					for hit in res['hits']['hits']:
						recHash = hashlib.sha1()
						data = {'sourceRecords': [], 'links': [], 'matchFields': {}}
						idbMatchList = []
						pbdbMatchList = []
						if hit['_type'] == 'idigbio':
							# Store the primary ID
							linkID = hit['_source']['idigbio:uuid']
							linkField= 'idigbio:uuid'

							if hit['_source'][localityMatch['idigbio']] is not None:
								pbdbMatchList.append({"match": {localityMatch['pbdb']: hit['_source'][localityMatch['idigbio']]}})
								idbMatchList.append({"match": {localityMatch['idigbio']: hit['_source'][localityMatch['idigbio']]}})
								data['matchFields'][localityMatch['idigbio']] = hit['_source'][localityMatch['idigbio']]
								recHash.update(data['matchFields'][localityMatch['idigbio']].encode('utf-8'))

							if hit['_source'][taxonMatch['idigbio']] is not None:
								pbdbMatchList.append({"match": {taxonMatch['pbdb']: hit['_source'][taxonMatch['idigbio']]}})
								idbMatchList.append({"match": {taxonMatch['idigbio']: hit['_source'][taxonMatch['idigbio']]}})
								data['matchFields'][taxonMatch['idigbio']] = hit['_source'][taxonMatch['idigbio']]
								recHash.update(data['matchFields'][taxonMatch['idigbio']])

							chrono_early = chrono_late = None
							ma_start_score = ma_end_score = 0
							ma_start = 1000
							ma_end = 0
							if hit['_source'][chronoMatch['idigbio']['early']] and hit['_source'][chronoMatch['idigbio']['late']]:
								chrono_early = hit['_source'][chronoMatch['idigbio']['early']]
								chrono_late = hit['_source'][chronoMatch['idigbio']['late']]
								idbMatchList.append({"match": {chronoMatch['idigbio']['early']: hit['_source'][chronoMatch['idigbio']['early']]}})
								idbMatchList.append({"match": {chronoMatch['idigbio']['late']: hit['_source'][chronoMatch['idigbio']['late']]}})
							elif hit['_source'][chronoMatch['idigbio']['early']]:
								chrono_early = hit['_source'][chronoMatch['idigbio']['early']]
								chrono_late = hit['_source'][chronoMatch['idigbio']['early']]
								idbMatchList.append({"match": {chronoMatch['idigbio']['early']: hit['_source'][chronoMatch['idigbio']['early']]}})
								idbMatchList.append({"match": {chronoMatch['idigbio']['late']: hit['_source'][chronoMatch['idigbio']['early']]}})
							elif hit['_source'][chronoMatch['idigbio']['late']]:
								chrono_early = hit['_source'][chronoMatch['idigbio']['late']]
								chrono_late = hit['_source'][chronoMatch['idigbio']['late']]
								idbMatchList.append({"match": {chronoMatch['idigbio']['early']: hit['_source'][chronoMatch['idigbio']['late']]}})
								idbMatchList.append({"match": {chronoMatch['idigbio']['late']: hit['_source'][chronoMatch['idigbio']['late']]}})
							if chrono_early and chrono_late:
								ma_start_res = self.es.search(index="chronolookup", body={"query": {"match": {"name": {"query": chrono_early, "fuzziness": "AUTO"}}}})
								for ma_start_hit in ma_start_res['hits']['hits']:
									if hit['_score'] > ma_start_score:
										ma_start = ma_start_hit["_source"]["start_ma"]
										ma_start_score = ma_start_hit['_score']
								ma_end_res = self.es.search(index="chronolookup", body={"query": {"match": {"name": {"query": chrono_late, "fuzziness": "AUTO"}}}})
								for ma_end_hit in ma_end_res['hits']['hits']:
									if hit['_score'] > ma_end_score:
										ma_end = ma_start_hit["_source"]["end_ma"]
										ma_end_score = ma_start_hit['_score']
								data['matchFields']['chronostratigraphy'] = [ma_start, ma_end]
								pbdbMatchList.append({"range": {"min_ma": {"lte": ma_start}}})
								pbdbMatchList.append({"range": {"max_ma": {"gte": ma_end}}})
							recHash.update(str(ma_start)+str(ma_end))

							linkQuery = {
								"size": 10,
								"query":{
									"bool":{
										"must": pbdbMatchList
									}
								}
							}
							queryIndex = 'pbdb'
						elif hit['_type'] == 'pbdb':
							if hit['_source'][localityMatch['pbdb']] is not None:
								idbMatchList.append({"match": {localityMatch['idigbio']: hit['_source'][localityMatch['pbdb']]}})
								pbdbMatchList.append({"match": {localityMatch['pbdb']: hit['_source'][localityMatch['pbdb']]}})
								data['matchFields'][localityMatch['pbdb']] = hit['_source'][localityMatch['pbdb']]
								recHash.update(data['matchFields'][localityMatch['pbdb']].encode('utf-8'))
							if hit['_source'][taxonMatch['pbdb']] is not None:
								idbMatchList.append({"match": {taxonMatch['idigbio']: hit['_source'][taxonMatch['pbdb']]}})
								pbdbMatchList.append({"match": {taxonMatch['pbdb']: hit['_source'][taxonMatch['pbdb']]}})
								data['matchFields'][taxonMatch['pbdb']] = hit['_source'][taxonMatch['pbdb']]
								recHash.update(data['matchFields'][taxonMatch['pbdb']])
							matchChrono = []
							ma_start_res = self.es.search(index="chronolookup", body={"query": {"match": {"start_ma": {"query": hit['_source']['min_ma']}}}})
							for ma_start_hit in ma_start_res['hits']['hits']:
								matchChrono.append(ma_start_hit['_source']['name'])
							ma_end_res = self.es.search(index="chronolookup", body={"query": {"match": {"name": {"query": hit['_source']['max_ma']}}}})
							for ma_end_hit in ma_end_res['hits']['hits']:
								matchChrono.append(ma_end_hit['_source']['name'])
							data['matchFields']['chronostratigraphy'] = matchChrono
							pbdbMatchList.append({"range": {"min_ma": {"lte": hit['_source']['min_ma']}}})
							pbdbMatchList.append({"range": {"max_ma": {"gte": hit['_source']['max_ma']}}})
							recHash.update(str(matchChrono))
							linkID = hit['_source']['occurrence_no']
							linkField = 'occurrence_no'
							linkQuery = {
								"size": 10,
								"query":{
									"bool":{
										"must": idbMatchList,
										"should": [
											{"terms": {"dwc:earliestAgeOrLowestStage": matchChrono}},
											{"terms": {"dwc:latestAgeOrHighestStage": matchChrono}},
											{"terms": {"dwc:earliestPeriodOrLowestSystem": matchChrono}},
											{"terms": {"dwc:latestPeriodOrHighestSystem": matchChrono}},
											{"terms": {"dwc:earliestEpochOrLowestSeries": matchChrono}},
											{"terms": {"dwc:latestEpochOrHighestSeries": matchChrono}},
											{"terms": {"dwc:earliestEraOrLowestErathem": matchChrono}},
											{"terms": {"dwc:latestEraOrHighestErathem": matchChrono}}
										]
									}
								}
							}
							queryIndex = 'idigbio'
						matches['search_after'] = json.dumps(hit["sort"])
						hashRes = recHash.hexdigest()

						if hashRes in matches['results']:
							sourceRow = self.resolveReference(hit["_source"], hit["_id"], hit["_type"])
							matches['results'][hashRes]['sources'].append(sourceRow)
						else:
							sourceRow = self.resolveReference(hit["_source"], hit["_id"], hit["_type"])
							matches['results'][hashRes] = {'fields': data['matchFields'], 'totalMatches': 0, 'matches': [], 'sources': [sourceRow]}

							linkResult = self.es.search(index=queryIndex, body=linkQuery)
							matchCount = 0
							totalMatches = linkResult['hits']['total']

							if matchCount > 0:
								linkResult = self.es.search(index=queryIndex, body=linkQuery)
							for link in linkResult['hits']['hits']:
								row = self.resolveReference(link['_source'], link['_id'], link['_type'])
								row['score'] = link['_score']
								row['type'] = link['_type']
								matches['results'][hashRes]['matches'].append(row)
								data['links'].append([link['_id'], link['_score'], link['_type']])
							matches['results'][hashRes]['totalMatches'] = totalMatches
							linkQuery['size'] = totalMatches
							matches['results'][hashRes]['fullMatchQuery'] = json.dumps(linkQuery)
							if queryIndex == 'idigbio':
								sourceQuery = {
									"query":{
										"bool":{
											"must": pbdbMatchList
										}
									}
								}

							elif queryIndex == 'pbdb':
								sourceQuery = {
									"query":{
										"bool":{
											"must": idbMatchList
										}
									}
								}
							matches['results'][hashRes]['fullSourceQuery'] = json.dumps(sourceQuery)
							matches['results'][hashRes]['fields'] = data['matchFields']
						if hit["_type"] == 'idigbio':
							sourceType = 'idigbio'
							matchType = 'pbdb'
						else:
							sourceType = 'pbdb'
							matchType = 'idigbio'
						matches['results'][hashRes]['sourceType'] = sourceType
						matches['results'][hashRes]['matchType'] = matchType
			matches['queryInfo'] = {'idigbioTotal': idbRes['hits']['total'], 'pbdbTotal': pbdbRes['hits']['total']}
			return matches
		else:
			return self.respondWithDescription()

	def description(self):
		return {
			'name': 'Occurrence Index',
			'maintainer': 'Michael Benowitz',
			'maintainer_email': 'michael@epandda.org',
			'description': 'Searches PBDB and iDigBio for occurrences and returns match groups based on taxonomy, chronostratigraphy and locality',
			'params': [
				{
					"name": "terms",
					"label": "Search terms",
					"type": "text",
					"required": False,
					"description": "Search field and term pairs. Formatted in a pipe delimited list with field and term separated by a colon. For example: genus:hadrosaurus|country:united states",
					"display": True
				},
				{
					"name": "matchOn",
					"label": "Match on",
					"type": "text",
					"required": False,
					"description": "By default this matches records based on locality, taxonomy and chronostratigraphy. Set this parameter to skip matching on one of these fields",
					"display": True
				},
				{
					"name": "chronoMatchLevel",
					"label": "Chronostratigraphy match level",
					"type": "text",
					"required": False,
					"description": "The geologic time unit to use in matching records. Allowed values: stage|system|series|erathem",
					"display": True
				},
				{
					"name": "taxonMatchLevel",
					"label": "Taxonomy match level",
					"type": "text",
					"required": False,
					"description": "The taxonomic rank to use in matching records. Allowed values: scientificName|genus|family|order|class|phylum|kingdom",
					"display": True
				},
				{
					"name": "localityMatchLevel",
					"label": "Locality match level",
					"type": "text",
					"required": False,
					"description": "The geographic unit to use in matching records. Allowed values: geopoint|locality|county|state|country",
					"display": True
				},
				{
					"name": "geoPointRadius",
					"label": "Maximum Distance from Geopoint",
					"type": "integer",
					"required": False,
					"description": "The maximum radius, in km, to return results within if querying on a geoPoint field",
					"display": True
				},
				{
					"name": "searchFrom",
					"label": "Last returned record to search from",
					"type": "text",
					"required": False,
					"description": "This implements paging in a more effecient way in ElasticSearch. It should be provided the last return search result in order to request a subsequent page of search results",
					"display": False
				}
			]}
