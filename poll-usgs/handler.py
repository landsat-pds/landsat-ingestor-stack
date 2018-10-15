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

def get_scene_list():
    SCENE_LIST_URL = 'http://landsat-pds.s3.amazonaws.com/c1/L8/scene_list.gz'
    scene_list_path = '/tmp/scene_list.gz'
    with open(scene_list_path, 'wb') as f:
        r = requests.get(SCENE_LIST_URL, stream=True)
        for block in r.iter_content(1024):
            f.write(block)

    tier = os.environ['TIER']

    with gzip.open(scene_list_path, 'rb') as f:
        # Parse the scene_list CSV. Check the tier using the product id in the first
        # column, and save the entity id in the second column.
        scene_list = [
            s.decode('utf-8').split(',')[1]
            for s in f.readlines()
            if s.decode('utf-8').split(',')[0].endswith(tier)
        ]
    scene_list.pop(0)
    return set(scene_list)


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

    days_prior = 4 if tier == 'RT' else 30
    start_date = (now - timedelta(days=days_prior)).strftime(fmt)
    end_date = now.strftime(fmt)

    # This field id represents the Collection Category
    where = {
        20510: tier
    }

    result = api.search(
            'LANDSAT_8_C1', 'EE', start_date=start_date, end_date=end_date, where=where, api_key=api_key)

    # Strangely, the entity id is still used to obtain a download url.
    entityIds = [
        scene['entityId'] for scene in result['data']['results']
    ]

    return entityIds


def main(event, context):

    entity_ids = poll_usgs()
    logger.info("Found {} scenes for potential queuing.".format(len(entity_ids)))
    print("Found {} scenes for potential queuing.".format(len(entity_ids)))

    scene_list = get_scene_list()
    logger.info("Found {} scenes that currently exist.".format(len(scene_list)))
    print("Found {} scenes that currently exist.".format(len(scene_list)))

    entity_ids = [s for s in entity_ids if s not in scene_list]
    logger.info("Queuing %d scenes to ingest" % len(entity_ids))
    print("Queuing %d scenes to ingest" % len(entity_ids))

    Construct the SQS URL. The Cloudformation template defines same name
    for this Lambda function and SQS.
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
