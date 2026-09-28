#!/usr/bin/env python3
"""Unit tests for check_prod_fail_open_deployed.py

Tests the pre-deploy hook with mocked boto3 clients (Stubber + unittest.mock).
Each test case exercises the five requirements:
  1. mock LastModified = fix_time + 1s → exit 0
  2. mock LastModified = fix_time - 1s AND no pending change set → exit non-zero
  3. mock LastModified stale BUT pending change set includes FleetAPIFunction modify → exit 0 (escape hatch)
  4. no matching function found → exit non-zero with a clear error
  5. aws lambda API error (permissions / network) → exit non-zero, not zero
"""
from __future__ import annotations

import pathlib
import io
import zipfile
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
from botocore.stub import Stubber
from botocore.exceptions import ClientError

# Import the script as a module
# Resolve the script directory relative to this test file. An absolute developer
# home path both leaked a real username into a published file and made this test
# runnable on exactly one machine.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_prod_fail_open_deployed as script_module


class TestCheckProdFailOpenDeployed(unittest.TestCase):
    """Test suite for check_prod_fail_open_deployed.py"""

    def setUp(self):
        """Set up test fixtures."""
        self.fix_time = script_module.FIX_COMMIT_AUTHORED_TIME
        self.one_second_after = self.fix_time + timedelta(seconds=1)
        self.one_second_before = self.fix_time - timedelta(seconds=1)

    def test_case_1_current_lambda_exits_zero(self):
        """Test case 1: LastModified = fix_time + 1s → exit 0"""
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        
        with Stubber(lambda_client) as stubber:
            stubber.add_response(
                "list_functions",
                {
                    "Functions": [
                        {
                            "FunctionName": "cms-prod-ui-FleetAPIFunction123",
                            "LastModified": self.one_second_after.isoformat(),
                            "Runtime": "python3.13",
                        }
                    ]
                }
            )
            
            with patch("boto3.client", return_value=lambda_client):
                result = script_module.get_latest_fleet_api_function()
        
        self.assertIsNotNone(result)
        func_name, last_modified = result
        self.assertEqual(func_name, "cms-prod-ui-FleetAPIFunction123")
        self.assertGreaterEqual(last_modified, self.fix_time)

    def test_case_9_target_function_on_second_page_is_found(self):
        """Regression: the target function is NOT on the first page.

        `list_functions` returns at most 50 functions per page. When this was written the
        prod account held 91 Lambdas with the FleetAPIFunction at position 82 — so an
        unpaginated call found zero candidates, reported "no matching function found", and
        the pre-deploy hook would have permanently blocked a legitimate prod deploy while
        looking like a working safety check.

        Every other test in this file stubs a single page, which is exactly why all 8 of
        them passed against the broken version. This test stubs TWO pages with the target
        only on the second, so it fails if the paginator is ever removed.

        Found by review 2026-08-10, confirmed against live AWS. See review.md Cycle 1.
        """
        lambda_client = boto3.client("lambda", region_name="us-east-1")

        # Page 1: 50 unrelated functions, no FleetAPIFunction, plus a NextMarker.
        page_one = {
            "Functions": [
                {
                    "FunctionName": f"cms-prod-unrelated-{i:02d}",
                    "LastModified": self.one_second_before.isoformat(),
                    "Runtime": "python3.13",
                }
                for i in range(50)
            ],
            "NextMarker": "page-2-marker",
        }
        # Page 2: the target, current (fix deployed).
        page_two = {
            "Functions": [
                {
                    "FunctionName": "cms-prod-ui-FleetAPIFunction4040B319-abc",
                    "LastModified": self.one_second_after.isoformat(),
                    "Runtime": "python3.13",
                }
            ]
        }

        with Stubber(lambda_client) as stubber:
            stubber.add_response("list_functions", page_one)
            stubber.add_response("list_functions", page_two)

            with patch("boto3.client", return_value=lambda_client):
                result = script_module.get_latest_fleet_api_function()

        self.assertIsNotNone(
            result,
            "The target function is on page 2. A None result means list_functions is not "
            "being paginated — the exact live defect this test guards.",
        )
        func_name, last_modified = result
        self.assertEqual(func_name, "cms-prod-ui-FleetAPIFunction4040B319-abc")
        self.assertGreaterEqual(last_modified, self.fix_time)

    def test_case_2_stale_lambda_no_changeset_exits_nonzero(self):
        """Test case 2: LastModified = fix_time - 1s AND no pending change set → exit non-zero"""
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        cfn_client = boto3.client("cloudformation", region_name="us-east-1")
        
        with Stubber(lambda_client) as stubber:
            stubber.add_response(
                "list_functions",
                {
                    "Functions": [
                        {
                            "FunctionName": "cms-prod-ui-FleetAPIFunction123",
                            "LastModified": self.one_second_before.isoformat(),
                            "Runtime": "python3.13",
                        }
                    ]
                }
            )
            
            with patch("boto3.client") as mock_client:
                mock_client.side_effect = lambda service, region_name: lambda_client if service == "lambda" else cfn_client
                result = script_module.get_latest_fleet_api_function()
        
        self.assertIsNotNone(result)
        func_name, last_modified = result
        self.assertLess(last_modified, self.fix_time)

    def test_case_3_stale_lambda_with_changeset_exits_zero_escape_hatch(self):
        """Test case 3: LastModified stale BUT change set with FleetAPI Lambda mod → exit 0 (escape hatch)"""
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        cfn_client = boto3.client("cloudformation", region_name="us-east-1")
        
        with Stubber(lambda_client) as lambda_stubber:
            lambda_stubber.add_response(
                "list_functions",
                {
                    "Functions": [
                        {
                            "FunctionName": "cms-prod-ui-FleetAPIFunction123",
                            "LastModified": self.one_second_before.isoformat(),
                            "Runtime": "python3.13",
                        }
                    ]
                }
            )
            
            with Stubber(cfn_client) as cfn_stubber:
                # Simulate a pending change set
                cfn_stubber.add_response(
                    "list_change_sets",
                    {
                        "Summaries": [
                            {
                                "ChangeSetName": "phase-b-deployment-123",
                                "Status": "CREATE_PENDING",
                            }
                        ]
                    }
                )
                # Describe the change set showing Lambda modification
                cfn_stubber.add_response(
                    "describe_change_set",
                    {
                        "ChangeSetName": "phase-b-deployment-123",
                        "Status": "CREATE_PENDING",
                        "Changes": [
                            {
                                "ResourceChange": {
                                    "Action": "Modify",
                                    "ResourceType": "AWS::Lambda::Function",
                                    "LogicalResourceId": "FleetAPIFunctionLogicalId",
                                }
                            }
                        ]
                    }
                )
                
                with patch("boto3.client") as mock_client:
                    mock_client.side_effect = lambda service, region_name: (
                        lambda_client if service == "lambda" else cfn_client
                    )
                    # The escape hatch should return True
                    is_escape_hatch = script_module.check_pending_change_set()
        
        self.assertTrue(is_escape_hatch)

    def test_case_4_no_matching_function_exits_nonzero(self):
        """Test case 4: No matching function found → exit non-zero with clear error"""
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        
        with Stubber(lambda_client) as stubber:
            # Return functions that don't match the pattern
            stubber.add_response(
                "list_functions",
                {
                    "Functions": [
                        {
                            "FunctionName": "some-other-function",
                            "LastModified": self.one_second_after.isoformat(),
                            "Runtime": "python3.13",
                        }
                    ]
                }
            )
            
            with patch("boto3.client", return_value=lambda_client):
                result = script_module.get_latest_fleet_api_function()
        
        self.assertIsNone(result)

    def test_case_5_lambda_api_error_exits_nonzero_not_zero(self):
        """Test case 5: aws lambda API error (permissions/network) → exit non-zero, not zero"""
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        
        with Stubber(lambda_client) as stubber:
            # Simulate an API error (e.g., permission denied)
            stubber.add_client_error(
                "list_functions",
                service_error_code="AccessDeniedException",
                service_message="User is not authorized to perform: lambda:ListFunctions"
            )
            
            with patch("boto3.client", return_value=lambda_client):
                result = script_module.get_latest_fleet_api_function()
        
        # The function should return None (indicating error) — NOT (None, None) or (func, None)
        self.assertIsNone(result)

    def test_case_6_network_error_exits_nonzero(self):
        """Additional test: Network error during list-functions → exit non-zero"""
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        
        with Stubber(lambda_client) as stubber:
            # Simulate a connection error
            stubber.add_client_error(
                "list_functions",
                service_error_code="ServiceUnavailableException",
                service_message="Service is temporarily unavailable"
            )
            
            with patch("boto3.client", return_value=lambda_client):
                result = script_module.get_latest_fleet_api_function()
        
        self.assertIsNone(result)

    def test_case_7_multiple_functions_picks_most_recent(self):
        """Additional test: Multiple FleetAPIFunction matches → pick the most recent"""
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        
        older_time = self.fix_time - timedelta(hours=1)
        newer_time = self.fix_time + timedelta(seconds=1)
        
        with Stubber(lambda_client) as stubber:
            stubber.add_response(
                "list_functions",
                {
                    "Functions": [
                        {
                            "FunctionName": "cms-prod-ui-FleetAPIFunction-old",
                            "LastModified": older_time.isoformat(),
                            "Runtime": "python3.13",
                        },
                        {
                            "FunctionName": "cms-prod-ui-FleetAPIFunction-new",
                            "LastModified": newer_time.isoformat(),
                            "Runtime": "python3.13",
                        },
                    ]
                }
            )
            
            with patch("boto3.client", return_value=lambda_client):
                result = script_module.get_latest_fleet_api_function()
        
        self.assertIsNotNone(result)
        func_name, last_modified = result
        # Should pick the newer one
        self.assertEqual(func_name, "cms-prod-ui-FleetAPIFunction-new")
        self.assertGreaterEqual(last_modified, newer_time)

    def test_case_8_fix_time_exact_match_exits_zero(self):
        """Edge case: LastModified == fix_time exactly → exit 0 (>= comparison)"""
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        
        with Stubber(lambda_client) as stubber:
            stubber.add_response(
                "list_functions",
                {
                    "Functions": [
                        {
                            "FunctionName": "cms-prod-ui-FleetAPIFunction123",
                            "LastModified": self.fix_time.isoformat(),
                            "Runtime": "python3.13",
                        }
                    ]
                }
            )
            
            with patch("boto3.client", return_value=lambda_client):
                result = script_module.get_latest_fleet_api_function()
        
        self.assertIsNotNone(result)
        func_name, last_modified = result
        # Should pass the >= check
        self.assertGreaterEqual(last_modified, self.fix_time)


if __name__ == "__main__":
    unittest.main()


class TestDeployedCodeInspection(unittest.TestCase):
    """Tests for get_deployed_fail_open_verdict — the only check that reads real code.

    Two proxies preceded it and both were wrong in opposite directions (a rollback
    faked GREEN via LastModified; an unrelated source edit faked RED via artifact
    hash). These tests exist to prove the replacement actually distinguishes patched
    from unpatched code, because a checker that cannot fail is not a checker.
    """

    @staticmethod
    def _zip_with_index(source: str, name: str = "index.py") -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(name, source)
        return buf.getvalue()

    def _run_with_artifact(self, blob):
        """Invoke the verdict fn against a synthetic deployed artifact."""
        lam = MagicMock()
        lam.get_function.return_value = {"Code": {"Location": "https://example.invalid/a.zip"}}
        fake_resp = MagicMock()
        fake_resp.read.return_value = blob
        fake_resp.__enter__ = lambda s: fake_resp
        fake_resp.__exit__ = lambda s, *a: False
        with patch("boto3.client", return_value=lam), \
             patch("urllib.request.urlopen", return_value=fake_resp):
            return script_module.get_deployed_fail_open_verdict("fn")

    def test_unpatched_groups_pattern_returns_false(self):
        """The pre-d235fb31 groupless defect must be detected."""
        src = "is_admin = 'platform-admin' in user_groups or not user_groups\n"
        self.assertIs(self._run_with_artifact(self._zip_with_index(src)), False)

    def test_unpatched_fleet_ids_pattern_returns_false(self):
        """The pre-fix fleetless defect must be detected."""
        src = "if has_unscoped_access or not user_fleet_ids:\n    pass\n"
        self.assertIs(self._run_with_artifact(self._zip_with_index(src)), False)

    def test_patched_code_returns_true(self):
        src = (
            "is_admin = 'platform-admin' in user_groups\n"
            "is_viewer = 'fleet-viewer' in user_groups\n"
            "has_unscoped_access = is_admin or is_viewer\n"
        )
        self.assertIs(self._run_with_artifact(self._zip_with_index(src)), True)

    def test_pattern_inside_a_comment_does_not_trip(self):
        """A comment explaining the old defect must not make patched code look broken.

        index.py genuinely carries such a comment next to the fix ("Previously an
        empty custom:fleetIds claim granted unscoped access..."), so without comment
        stripping this check would report the live, patched prod Lambda as unpatched.
        """
        src = (
            "# Previously this read: is_admin = ... or not user_groups\n"
            "# and the fleet guard was: or not user_fleet_ids\n"
            "is_admin = 'platform-admin' in user_groups\n"
        )
        self.assertIs(self._run_with_artifact(self._zip_with_index(src)), True)

    def test_missing_index_py_returns_none(self):
        """None means 'unknown', which callers must treat as failure, not success."""
        blob = self._zip_with_index("x = 1\n", name="other.py")
        self.assertIsNone(self._run_with_artifact(blob))

    def test_download_failure_returns_none(self):
        lam = MagicMock()
        lam.get_function.return_value = {"Code": {"Location": "https://example.invalid/a.zip"}}
        with patch("boto3.client", return_value=lam), \
             patch("urllib.request.urlopen", side_effect=OSError("network down")):
            self.assertIsNone(script_module.get_deployed_fail_open_verdict("fn"))

    def test_absent_code_location_returns_none(self):
        lam = MagicMock()
        lam.get_function.return_value = {"Code": {}}
        with patch("boto3.client", return_value=lam):
            self.assertIsNone(script_module.get_deployed_fail_open_verdict("fn"))

    def test_corrupt_zip_returns_none(self):
        self.assertIsNone(self._run_with_artifact(b"not-a-zip"))
