
import os
import json
import boto3

# boto3.setup_default_session(profile_name='landsat', region_name='us-west-2')
s3 = boto3.client('s3')

# boto3.setup_default_session(profile_name='default', region_name='us-west-2')
batch = boto3.client('batch')

def aggregate_run(array_job_id):
    """
    After a run is complete, aggregate the results into a CSV file.
    """
    paginator = s3.get_paginator('list_objects_v2')
    kwargs = {
        'Bucket': 'landsat-pds',
        'EncodingType': 'url',
        'Prefix': '{}/'.format(array_job_id),
        'FetchOwner': False,
        'PaginationConfig': {
            'MaxItems': 10000,
            'PageSize': 1000
        }
    }
    response_iterator = paginator.paginate(**kwargs)
    rows = []
    for page in response_iterator:
        for item in page['Contents']:
            if item['Key'].endswith('.csv'):
                obj = s3.get_object(Bucket='landsat-pds', Key=item['Key'])
                rows += [obj['Body'].read().split('\n')]

    names, entries, _ = zip(*rows)
    csv_list = [names[0]] + list(entries)
    csv_str = "\n".join(csv_list)

    print csv_str

    # TODO: Delete objects using s3.delete_objects
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.delete_objects
    # TODO: Upload run CSV file. This requires shutting down other service to avoid collisions.


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
    max_items = 4
    # max_items = 5000

    paginator = s3.get_paginator('list_objects_v2')
    kwargs = {
        'Bucket': 'landsat-pds',
        'EncodingType': 'url',
        'Prefix': 'tarq/',
        'FetchOwner': False,
        'PaginationConfig': {
            'MaxItems': max_items,
            'PageSize': 1000
        }
    }
    response_iterator = paginator.paginate(**kwargs)
    items = [
        item['Key']
        for page in response_iterator
        for item in page['Contents']
        if item['Key'].endswith('.tar.gz')
    ]

    # if len(items) < max_items:
    #     # Check the tarq_archive directory
    #     kwargs['Prefix'] = 'tarq_archive/'
    #     response_iterator = paginator.paginate(**kwargs)
    #     items += [
    #         item['Key']
    #         for page in response_iterator
    #         for item in page['Contents']
    #     ]

    if len(items) == 0:
        print("No work to be done")
        return

    for item in items:
        print(item)

    response = s3.put_object(
        Bucket='landsat-pds',
        Key='run_list.txt',
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

        a. Check the tarq (tarq_archive) directory on S3 for scene tarballs
           to be processed.
        b. Update the run state in run_info.json
        c. Upload a list of scenes to process to S3
        d. Submit an array job to be processed by AWS Batch
    """
    RUN_INFO_KEY = 'run_info_dev.json'

    # Check run state by probing run_info.json
    run_info_object = s3.get_object(
        Bucket='landsat-pds', Key=RUN_INFO_KEY)
    run_info = json.loads(
        run_info_object['Body'].read())

    if run_info['active_run'] is None:
        array_job_id = populate_queue()
        run_info['active_run'] = array_job_id
        print(run_info)
        response = s3.put_object(
            Bucket='landsat-pds',
            Key=RUN_INFO_KEY,
            Body=json.dumps(run_info)
        )
        return

    # An active run is processing. Poll the Batch queue
    # to determine if the run is complete.
    array_job_id = run_info['active_run']
    if is_batch_complete(array_job_id):
        aggregate_run(array_job_id)

        # TODO: Append new entries to the scene_list

        print("Run with job id of {} is complete".format(array_job_id))
        run_info['active_run'] = None
        run_info['last_run'] = run_info['last_run'] + 1
        response = s3.put_object(
            Bucket='landsat-pds',
            Key=RUN_INFO_KEY,
            Body=json.dumps(run_info)
        )
        return

    print("Run is active with job id of {}".format(array_job_id))
    return