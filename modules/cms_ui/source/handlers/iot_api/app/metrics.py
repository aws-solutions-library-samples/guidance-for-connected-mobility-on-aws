from datetime import datetime, timedelta, timezone
from app.schema import Metric
from utils.logger import logger


IOT_METRIC_MAPPING = [
    {
        "name": "incoming.messages",
        "label": "INCOMING_MESSAGES",
        "raw_metrics": ["PublishIn.Success"],
        "dimensions": [
            {"Name": "Protocol", "Value": "MQTT"},
        ],
    },
    {
        "name": "outgoing.messages",
        "label": "OUTGOING_MESSAGES",
        "raw_metrics": ["PublishOut.Success"],
        "dimensions": [
            {"Name": "Protocol", "Value": "MQTT"},
        ],
    },
    {
        "name": "dropped.messages",
        "label": "DROPPED_MESSAGES",
        "raw_metrics": ["Queued.Throttle", "Queued.ServerError"],
        "dimensions": [
            {"Name": "Protocol", "Value": "MQTT"},
        ],
    },
    {
        "name": "incoming.errors",
        "label": "INCOMING_ERRORS",
        "raw_metrics": [
            "PublishIn.AuthError",
            "PublishIn.ClientError",
            "PublishIn.ServerError",
            "PublishIn.Throttle",
        ],
        "dimensions": [
            {"Name": "Protocol", "Value": "MQTT"},
        ],
    },
    {
        "name": "outgoing.errors",
        "label": "OUTGOING_ERRORS",
        "raw_metrics": [
            "PublishOut.AuthError",
            "PublishOut.ClientError",
            "PublishOut.ServerError",
            "PublishOut.Throttle",
        ],
        "dimensions": [
            {"Name": "Protocol", "Value": "MQTT"},
        ],
    },
    {
        "name": "rules.executed",
        "label": "RULES_EXECUTED",
        "raw_metrics": [
            "RulesExecuted",
        ],
        "dimensions": [],
    },
]


class MetricsUtil:

    default_namespace = "AWS/IoT"

    def __init__(self, cwl_client) -> None:
        self._cwl_client = cwl_client

    def calculate_period(self, start_time: datetime, end_time: datetime):
        # period (minutes) can be 1, 5, 15, 60, 360
        # Automatically calculate the best value for period, start_time, end_time for CloudWatch API call.
        # Base on below:
        # For better performance, specify StartTime and EndTime values that align with the value of the metric’s Period
        #  and sync up with the beginning and end of an hour.
        # For example, if the Period of a metric is 5 minutes,
        # specifying 12:05 or 12:30 as StartTime can get a faster response
        # from CloudWatch than setting 12:07 or 12:29 as the StartTime.

        # delta in seconds.
        delta = end_time - start_time
        delta_seconds = delta.total_seconds()
        # logger.info(start_time.strftime("%H:%M:%S"))
        # logger.info(end_time.strftime("%H:%M:%S"))
        # logger.info(delta.seconds)

        # Apply a 1-minute's delay on metric data.
        start_time = start_time - timedelta(minutes=1)
        end_time = end_time - timedelta(minutes=1)

        if delta_seconds <= 30 * 60:
            # if time range is <= 30 minutes, set period to 1 minute
            period = 60
            truncated_start_time = start_time.replace(second=0, microsecond=0)
            truncated_end_time = end_time.replace(second=0, microsecond=0)
        elif delta_seconds <= 150 * 60:
            # if time range is <= 150 minutes, set period to 5 minutes
            period = 5 * 60
            truncated_start_time = start_time.replace(
                minute=start_time.minute // 5 * 5, second=0, microsecond=0
            )
            truncated_end_time = end_time.replace(
                minute=end_time.minute // 5 * 5, second=0, microsecond=0
            )
        elif delta_seconds <= 30 * 60 * 60:
            # if time range is <= 30 hours, set period to 1 hour
            truncated_start_time = start_time.replace(minute=0, second=0, microsecond=0)
            truncated_end_time = end_time.replace(minute=0, second=0, microsecond=0)
            period = 60 * 60
        elif delta_seconds <= 7 * 24 * 60 * 60:
            # if time range is <= 7 days, set period to 6 hours
            period = 6 * 60 * 60
            truncated_start_time = start_time.replace(minute=0, second=0, microsecond=0)
            truncated_end_time = end_time.replace(minute=0, second=0, microsecond=0)
        else:
            # set period to 1 day
            period = 24 * 60 * 60
            truncated_start_time = start_time.replace(minute=0, second=0, microsecond=0)
            truncated_end_time = end_time.replace(minute=0, second=0, microsecond=0)

        logger.info(truncated_start_time.strftime("%H:%M:%S"))
        logger.info(truncated_end_time.strftime("%H:%M:%S"))

        return period, truncated_start_time, truncated_end_time

    def _build_metric_query(
        self,
        metric_list: list[Metric],
        *,
        statistic_type="Sum",
        period: int = 60,
        group: bool = False,
        use_search: bool = False,
    ) -> list:
        """Build Args for GetMetricData Call.

        Ref Doc:
        https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/cloudwatch/client/get_metric_data.html
        """

        metric_queries = []
        for i, metric in enumerate(metric_list):

            if use_search:
                # Use search expression.
                search_template = "SEARCH('{{{namespace}, {dimension_list}}} {filters}', '{statistic_type}', {period})"
                filters = f'MetricName=\\"{metric.metric_name}\\"'
                for d in metric.dimensions:
                    if d["Value"] == "*":
                        continue
                    filters += f" {d['Name']}=\\\"{d['Value']}\\\""

                search_expression = search_template.format(
                    namespace=metric.namespace,
                    dimension_list=", ".join([d["Name"] for d in metric.dimensions]),
                    filters=filters,
                    statistic_type=statistic_type,
                    period=period,
                )
                if group:
                    search_expression = f"SUM({search_expression})"

                metric_queries.append(
                    {
                        "Id": f"m{i}",
                        "Expression": search_expression,
                        "Label": metric.metric_name,
                    }
                )
            else:
                metric_queries.append(
                    {
                        "Id": f"m{i}",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": metric.namespace,
                                "MetricName": metric.metric_name,
                                "Dimensions": metric.dimensions,
                            },
                            "Period": period,
                            "Stat": statistic_type,
                            # "Unit": "Count",
                        },
                        "ReturnData": True,
                        "Label": metric.metric_name,
                    }
                )

        return metric_queries

    def get_metric_data(
        self,
        metric_list: list[Metric],
        start_time: datetime,
        end_time: datetime,
        *,
        group: bool = False,
        use_search: bool = False,
    ) -> tuple[list[int], dict[str, list[int]]]:
        """Get a tuple of (timestamps, data points) for metrics.

        Example response:

        timestamps: [1729492800, 1729493100, 1729493400]
        data: {
            "metric1": [1,2,3],
            "metric2": [3,4,5],
        }

        """
        # calculate best value to call APIs
        period, truncated_start_time, truncated_end_time = self.calculate_period(
            start_time, end_time
        )

        # get x-axis (a list of timestamp)
        xaxis = list(
            range(
                int(truncated_start_time.timestamp()),
                int(truncated_end_time.timestamp()),
                period,
            )
        )
        logger.info(xaxis)
        # Create a {timestamp: index} dict
        xaxis_indices = {}
        for i, t in enumerate(xaxis):
            xaxis_indices[t] = i

        # initialize zero data.
        data = {}
        for m in metric_list:
            data[m.metric_name] = [0 for _ in xaxis]
        # logger.info(data)

        metric_queries = self._build_metric_query(
            metric_list,
            period=period,
            group=group,
            use_search=use_search,
        )
        # logger.info(metric_queries)

        try:
            # Call get_metric_data API
            response = self._cwl_client.get_metric_data(
                MetricDataQueries=metric_queries,
                StartTime=truncated_start_time.timestamp(),
                EndTime=truncated_end_time.timestamp(),
                ScanBy="TimestampDescending",
            )

            # Process the response
            if "MetricDataResults" in response:
                for result in response["MetricDataResults"]:
                    # logger.info(f"Metric ID: {result['Id']}")
                    # logger.info(f"Label: {result['Label']}")
                    # logger.info(f"Timestamps: {result['Timestamps']}")
                    # logger.info(f"Values: {result['Values']}")
                    for i, ts in enumerate(result["Timestamps"]):
                        # Update value by metrics name and index
                        ts_index = xaxis_indices.get(int(ts.timestamp()))
                        data[result["Label"]][ts_index] += result["Values"][i]

            else:
                logger.info("No metric data found.")

        except Exception as e:
            logger.info(f"An error occurred: {str(e)}")

        return xaxis, data

    def get_metric_statistics(
        self,
        metric_list: list[Metric],
        *,
        number_of_days=1,
        statistic_type="Sum",
        group: bool = False,
        use_search: bool = False,
    ) -> dict[str, int]:
        """Get metric statistics for last N days.

        Data got refreshed every 5 minutes.

        Example response:
        {
            "metric1": 100,
            "metric2": 200,
        }
        """

        tz = timezone.utc
        now = datetime.now(tz)
        start = now - timedelta(days=number_of_days)

        truncated_start_time = start.replace(
            minute=start.minute // 5 * 5, second=0, microsecond=0
        )
        truncated_end_time = now.replace(
            minute=now.minute // 5 * 5, second=0, microsecond=0
        )
        period = 60 * 60
        data = {}

        metric_queries = self._build_metric_query(
            metric_list,
            period=period,
            statistic_type=statistic_type,
            group=group,
            use_search=use_search,
        )

        try:
            # Call get_metric_data API
            response = self._cwl_client.get_metric_data(
                MetricDataQueries=metric_queries,
                StartTime=truncated_start_time.timestamp(),
                EndTime=truncated_end_time.timestamp(),
                ScanBy="TimestampDescending",
            )

            # Process the response
            if "MetricDataResults" in response:
                for result in response["MetricDataResults"]:
                    # logger.info(f"Metric ID: {result['Id']}")
                    # logger.info(f"Label: {result['Label']}")
                    # logger.info(f"Timestamps: {result['Timestamps']}")
                    # logger.info(f"Values: {result['Values']}")
                    for v in result["Values"]:
                        if group:
                            label = result["Label"].split(" ")[0]
                        else:
                            label = result["Label"]
                        data[label] = data.get(label, 0) + int(v)

            else:
                logger.info("No metric data found.")
        except Exception as e:
            logger.info(f"An error occurred: {str(e)}")
        return data
