
import gzip
import hashlib
from datetime import date, timedelta

import requests
import boto3


def chunks(l):
    for i in range(0, len(l), 10):
        yield l[i:i+10]


def create_path_row_tree(scenes):
    """
    Create a search tree using path/row as indices.
    """
    tree = {
        '%03d' % p: {
            '%03d' % r: []
            for r in range(1, 249)
        } for p in range(1, 234)
    }

    for s in scenes:
        path, row = s[3:6], s[6:9]
        tree[path][row].append(s)

    return tree


def in_scene_tree(scene, tree):
    path, row = scene[3:6], scene[6:9]
    return True if scene in tree[path][row] else False


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
        if i == 1500:
            break

    entries = output.decode('utf-8').split('\n')
    entries.pop(0)
    sceneids = [ s.split(',')[0] for s in entries ]

    today = date.today()
    yesterday = today - timedelta(1)
    year = str(yesterday.timetuple().tm_year)
    doy = str(yesterday.timetuple().tm_yday)

    criterion = year + doy
    return [ s for s in sceneids if criterion in s ]


def main(event, context):

    entity_ids = poll_usgs()
    scene_list = get_scene_list()

    tree = create_path_row_tree(scene_list)
    entity_ids = [ s for s in entity_ids if not in_scene_tree(s, tree) ]

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
