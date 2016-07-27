
import gzip
import hashlib
import logging

import requests
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def chunks(l):
    for i in range(0, len(l), 10):
        yield l[i:i+10]


class SceneTree(object):

    def __init__(self, scene_list):

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


def get_scene_list():
    """
    Get the scene list hosted on landsat-pds. This will be used
    to ensure scenes are not ingested more than once.
    """
    SCENE_LIST_URL = 'http://landsat-pds.s3.amazonaws.com/scene_list.gz'
    scene_list_path = '/tmp/scene_list.gz'
    with open(scene_list_path, 'wb') as f:
        r = requests.get(SCENE_LIST_URL, stream=True)
        for block in r.iter_content(1024):
            f.write(block)

    with gzip.open(scene_list_path, 'rb') as f:
        scene_list = [ s.decode('utf-8').split(',')[0] for s in f.readlines() ]
    scene_list.pop(0)

    return scene_list


def poll_usgs():
    """
    Poll the bulk metadata file for recent scenes. Only
    a portion of the file is downloaded.
    """
    L8_METADATA_URL = "http://landsat.usgs.gov/metadata_service/bulk_metadata_files/LANDSAT_8.csv"

    output = b""
    r = requests.get(L8_METADATA_URL, stream=True)
    for i, chunk in enumerate(r.iter_content(1024)):
        output += chunk

        # Read approximately 5MB of the CSV. This is roughly 14 days
        # of scenes. Being liberal with the date range allows us to
        # pick up any scenes that might have been accidently skipped
        # over the last ~2 weeks.
        if i == 5000:
            break

    entries = output.decode('utf-8').split('\n')
    entries.pop(0)
    entries.pop()
    return [ s.split(',')[0] for s in entries ]


def main(event, context):

    entity_ids = poll_usgs()
    scene_list = get_scene_list()

    tree = SceneTree(scene_list)
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
