# Landsat Ingestor Stack

This is a Cloudformation stack that copies Landsat 8 scenes from USGS to S3 (`landsat-pds`) on a regular basis. It is run and supported by Planet Labs.

## Architecture

 * AWS Lambda function polls USGS on a daily basis for refresh Landsat 8 scenes.
 * the list of scenes is added to SQS
 * when the queue is populated, and autoscaling event is triggered to start 5 instances.
 * each instance copies two scenes in parallel
 * when all scenes are complete an autoscaling action triggers a scale down event
