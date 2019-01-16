
import os
import json
import gzip
import boto3

s3 = boto3.client('s3')
batch = boto3.client('batch')

BUCKET = 'landsat-pds'
RUN_INFO_KEY = 'run_info.json'
RUN_LIST_KEY = 'run_list.txt'
SCENE_LIST_KEY = 'c1/L8/scene_list.gz'


def complete_run(run_info):
    """
    After a run is complete, aggregate the results into a CSV file.
    """
    array_job_id = run_info['active_run']
    last_run = run_info['last_run']

    paginator = s3.get_paginator('list_objects_v2')
    kwargs = {
        'Bucket': BUCKET,
        'EncodingType': 'url',
        'Prefix': '{}/'.format(array_job_id),
        'FetchOwner': False,
        'PaginationConfig': {
            'MaxItems': 10000,
            'PageSize': 1000
        }
    }
    rows = []
    for page in paginator.paginate(**kwargs):
        for item in page['Contents']:
            obj = s3.get_object(Bucket=BUCKET, Key=item['Key'])
            rows += [obj['Body'].read().split('\n')]

    names, entries, _ = zip(*rows)
    csv_list = [names[0]] + list(entries)
    csv_str = "\n".join(csv_list)

    print(csv_str)

    # Upload run CSV file
    # TODO: Migrate to production file
    run_info['active_run'] = None
    response = s3.put_object(
        Bucket=BUCKET,
        Key="runs_dev/{}.csv".format(last_run + 1),
        Body=csv_str,
        ACL='public-read'
    )

    # Fetch the scene_list
    filepath = '/tmp/scene_list.gz'
    s3.download_file(BUCKET, SCENE_LIST_KEY, filepath)
    with gzip.open(filepath) as f:
        scene_list = f.read()

    scene_list += ("\n".join(entries) + "\n")

    with gzip.open(filepath, 'wb') as f:
        f.write(scene_list)

    # Upload updated scene_list.gz file.
    response = s3.upload_file(
        '/tmp/scene_list.gz', BUCKET, SCENE_LIST_KEY, ExtraArgs={'ACL':'public-read'})

    # Delete CSV objects
    for page in paginator.paginate(**kwargs):
        s3.delete_objects(
            Bucket=BUCKET,
            Delete={
                'Objects': [
                    {
                        'Key': item['Key']
                    }
                    for item in page['Contents']
                ]
            }
        )


def populate_queue():
    """
    Incoming Landsat scenes are downloaded from USGS and stored in
    their original tarball form in s3://landsat-pds/tarq/. This
    function:

        * checks which tarballs are waiting to be unpacked and processed
        * creates a temporary list, and uploads it to S3, to be used by AWS Batch
        * submits an array job to an AWS Batch queue

    .. note:: Currently the entire Collection 1 T1 archive is being
              copied from USGS. While there is still work left to be
              done, this function will fallback to s3://landsat-pds/tarq_archive
              to process the backlog.
    """
    max_items = 100

    paginator = s3.get_paginator('list_objects_v2')
    items = []
    for prefix in ['tarq/', 'tarq_archive/']:
        kwargs = {
            'Bucket': BUCKET,
            'EncodingType': 'url',
            'Prefix': prefix,
            'FetchOwner': False,
            'PaginationConfig': {
                'MaxItems': max_items,
                'PageSize': 1000
            }
        }
        response_iterator = paginator.paginate(**kwargs)
        items += [
            item['Key']
            for page in response_iterator
            for item in page['Contents']
            if item['Key'].endswith('.tar.gz')
        ]

    if len(items) == 0:
        print("No work to be done")
        return

    for item in items:
        print(item)

    response = s3.put_object(
        Bucket=BUCKET,
        Key=RUN_LIST_KEY,
        Body='\n'.join(items),
        ACL='public-read'
    )

    job_queue = os.environ.get('AWS_BATCH_JOB_QUEUE')
    job_definition = os.environ.get('AWS_BATCH_JOB_DEFINITION')
    response = batch.submit_job(
        jobName='process-landsat',
        jobQueue=job_queue,
        arrayProperties={
            'size': len(items)
        },
        jobDefinition=job_definition
    )

    return response['jobId']


def is_batch_complete(array_job_id):
    """
    Poll the queue using the array job id. Since jobs are left in queue for 24 hours, check
    that the created time is after a cached time.

    :param array_job_id:
        The job id that was returned when a batch was submitted.

    Ref: https://docs.aws.amazon.com/batch/latest/userguide/job_states.html
    """
    job_statuses = [
        'SUBMITTED', 'PENDING', 'RUNNABLE', 'STARTING', 'RUNNING', 'SUCCEEDED', 'FAILED']
    job_status_counts = {
        job_status: 0 for job_status in job_statuses}

    kwargs = {
        'arrayJobId': array_job_id,
        'jobStatus': None,
        'maxResults': 100
    }

    for job_status in job_statuses:
        kwargs['jobStatus'] = job_status

        jobs = []
        while True:
            response_iterator = batch.list_jobs(**kwargs)
            jobs += [
                job for job in response_iterator['jobSummaryList']
            ]
            if 'nextToken' in response_iterator:
                kwargs['nextToken'] = response_iterator['nextToken']
                continue
            break
        
        kwargs.pop('nextToken', None)
        job_status_counts[job_status] = len(jobs)

    total_jobs = sum([
        value for k, value in job_status_counts.items()])

    completed_jobs = job_status_counts['SUCCEEDED'] + job_status_counts['FAILED']

    if completed_jobs == total_jobs:
        if job_status_counts['FAILED'] > 0:
            # TODO: Alert if any failures
            pass
        return True

    return False


def main(event, context):
    """
    This function orchestrates the batch processing of Landsat tarballs
    waiting in queue on S3. It has various responsibilities depending on
    the state of jobs.

    A run is the processing of a batch of scenes. Due to prior constraints
    only a single run is allowed, no concurrent runs are permitted. Run
    state is stored in a file on S3 (s3://landsat-pds/run_info.json).

    Check the run state.

    For an active run:

        a. Check if the jobs associated with the run are complete
        b. If all jobs are complete, aggregate the results by:
         * uploading a new run CSV file
         * appending new entries to the scene_list,
         * updating the run state to inactive in run_info.json.
        c. If jobs are not complete, no work is required.

    For no active run:

        a. Check the tarq and tarq_archive directory on S3 for
           scene tarballs to be processed.
        b. Update the run state in run_info.json
        c. Upload a list of scenes (run_list.txt) to process to S3
        d. Submit an array job to be processed by AWS Batch
    """

    # Check run state by probing run_info.json
    run_info_object = s3.get_object(
        Bucket=BUCKET, Key=RUN_INFO_KEY)
    run_info = json.loads(
        run_info_object['Body'].read())

    if run_info['active_run'] is None:
        array_job_id = populate_queue()
        run_info['active_run'] = array_job_id
        print(run_info)
        response = s3.put_object(
            Bucket=BUCKET,
            Key=RUN_INFO_KEY,
            Body=json.dumps(run_info),
            ACL='public-read'
        )
        return

    # An active run is processing. Poll the Batch queue
    # to determine if the run is complete.
    array_job_id = run_info['active_run']
    if is_batch_complete(array_job_id):
        complete_run(run_info)

        run_info['active_run'] = None
        run_info['last_run'] = run_info['last_run'] + 1
        response = s3.put_object(
            Bucket=BUCKET,
            Key=RUN_INFO_KEY,
            Body=json.dumps(run_info),
            ACL='public-read'
        )
        print("Run with job id of {} is complete".format(array_job_id))
        return

    print("Run is active with job id of {}".format(array_job_id))
    return