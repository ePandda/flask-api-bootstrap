from flask_restful import Resource, Api
from flask import current_app, url_for
from base import baseResource

#
# Boolean
#
class query(baseResource):
    def process(self):

        query_list = self.getQueryList()

        queries = []
        for q in query_list:
            if "endpoint" not in q or "parameters" not in q:
                continue

            try:
                endpoint = self.loadEndpoint(q['endpoint'])
            except Exception as e:
                continue

            if endpoint is not None:
                endpoint.returnResponse = False
                endpoint.setParams(q['parameters'])

                queries.append(endpoint.process())

        return self.respond({
          'queries': queries
        }, "queries")