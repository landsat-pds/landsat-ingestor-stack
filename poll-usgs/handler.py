#!/usr/bin/env python

import os
import gzip
import hashlib
import logging
from datetime import datetime, timedelta

import requests
import boto3

from usgs import api

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def chunks(l):
    for i in range(0, len(l), 10):
        yield l[i:i+10]


class SceneTree(object):


    def __init__(self):

        SCENE_LIST_URL = 'http://landsat-pds.s3.amazonaws.com/c1/L8/scene_list.gz'
        scene_list_path = '/tmp/scene_list.gz'
        with open(scene_list_path, 'wb') as f:
            r = requests.get(SCENE_LIST_URL, stream=True)
            for block in r.iter_content(1024):
                f.write(block)

        with gzip.open(scene_list_path, 'rb') as f:
            scene_list = [ s.decode('utf-8').split(',')[1] for s in f.readlines() ]
        scene_list.pop(0)

        self.data = {
            '%03d' % p: {
                '%03d' % r: []
                for r in range(1, 249)
            } for p in range(1, 234)
        }

        for s in scene_list:
            path, row = s[3:6], s[6:9]
            self.data[path][row].append(s)


    def __contains__(self, scene):
        path, row = scene[3:6], scene[6:9]
        return True if scene in self.data[path][row] else False


def poll_usgs():
    """
    Check whether USGS has made any new scenes available. In the case of RT scenes,
    we check only a few days back. In the case of T1/T2 scenes we check 4 weeks back due to
    processing latencies.
    """
    api_key = api.login(os.environ['USGS_USERNAME'], os.environ['USGS_PASSWORD'], save=False)['data']
    tier = os.environ['TIER']

    now = datetime.now()
    fmt = '%Y-%m-%d'

    if (tier == 'RT'):
        start_date = (now - timedelta(days=4)).strftime(fmt)
        end_date = now.strftime(fmt)
        dates = [(start_date, end_date)]
    else:
        dates = [
            (
                (now - timedelta(days=7 * (i + 1))).strftime(fmt),
                (now - timedelta(days=7 * i)).strftime(fmt)
            )
            for i in range(4)
        ]

    # This field id represents the Collection Category
    where = {
        20510: tier
    }

    entityIds = []
    for (start_date, end_date) in dates:
        result = api.search('LANDSAT_8_C1', 'EE', start_date=start_date, end_date=end_date, where=where, api_key=api_key)
        entityIds += [
            scene['entityId']
            for scene in result['data']['results']
        ]

    # Strangely, the entity id is still used to obtain a download url.
    return [
        scene['entityId']
        for scene in result['data']['results']
    ]


def main(event, context):

    entity_ids = poll_usgs()

    tree = SceneTree()
    entity_ids = [ s for s in entity_ids if s not in tree ]
    logger.info("Queuing %d scenes to ingest" % len(entity_ids))

    # Construct the SQS URL. The Cloudformation template defines same name
    # for this Lambda function and SQS.
    _, _, resource, region, account_id, _, name = context.invoked_function_arn.split(":")
    queue_url = "https://sqs.%s.amazonaws.com/%s/%s" % (region, account_id, name)

    client = boto3.client('sqs')

    responses = []
    for chunk in chunks(entity_ids):
        entries = [
            {
                "Id": hashlib.md5(entity_id).hexdigest()[0:6],
                "MessageBody": entity_id
            }
            for entity_id in chunk
        ]
        params = {
            "QueueUrl": queue_url,
            "Entries": entries
        }
        r = client.send_message_batch(**params)
        responses.append(r)

    return responses
