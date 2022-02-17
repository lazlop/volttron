from flask import Flask
from flask_restful import Resource, Api, reqparse
import pandas as pd
import ast
import json
import csv

app = Flask(__name__)
api = Api(app)

class MPC_interface(Resource):
    # Can save the previous data, and just send the new data needed for the next forecast?
    # Also can just send all the data needed for a timestep to a single method
    # can go from dataframe to json in volttron agent
    def post(self):
#         parser = reqparse.RequestParser()
#         parser.add_argument('data_json', required=True) 
#         in_data = pd.read_json(args['data_json'])
#         in_data.to_csv('test_flask.csv')
        return {'message':'saved correctly'}, 200

class test(Resource):
    def get(self):
        return 'text', 200

api.add_resource(MPC_interface, '/MPC_interface')
api.add_resource(test,'/test')

if __name__ == '__main__':
    app.run()  # run our Flask app


