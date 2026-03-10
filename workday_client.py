"""
Workday Integration Service Client.
Handles validation, status checking, and diagnostic queries for Workday integrations.
"""
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


COMMON_WORKDAY_INTEGRATIONS = [
    "Core Connector: Worker",
    "Core Connector: Payroll",
    "EIB (Enterprise Interface Builder)",
    "Studio Integration",
    "RaaS (Reporting as a Service)",
    "PECI Payroll Integration",
    "Benefits Integration",
    "Recruiting Integration (Greenhouse, Lever)",
    "Single Sign-On (SSO)",
    "Active Directory/LDAP Sync",
    "Background Check Integration",
    "Learning Management System (LMS)",
    "Expense Integration",
    "Document Management Integration",
]

ERROR_PATTERNS = {
    "authentication_failure": [
        "invalid credentials",
        "401 unauthorized",
        "authentication failed",
        "token expired",
        "oauth error",
        "sso failure",
        "invalid client_id",
    ],
    "data_validation_error": [
        "invalid data format",
        "required field missing",
        "constraint violation",
        "invalid worker id",
        "employee not found",
        "invalid cost center",
        "invalid position",
    ],
    "connectivity_issue": [
        "connection timeout",
        "connection refused",
        "network unreachable",
        "dns resolution failed",
        "ssl handshake failed",
        "endpoint not reachable",
    ],
    "business_process_error": [
        "business process validation",
        "approval not found",
        "org structure mismatch",
        "security group",
        "domain security policy",
        "worklet error",
    ],
    "data_mapping_error": [
        "field mapping",
        "transformation error",
        "xslt error",
        "invalid xml",
        "schema validation",
        "unmapped field",
    ],
    "integration_system_error": [
        "integration system user",
        "isu credential",
        "integration timeout",
        "batch failure",
        "launch parameter",
    ],
}


class WorkdayClient:
    """Client for Workday REST and SOAP API interactions."""

    def __init__(self):
        self.tenant_url = settings.WORKDAY_TENANT_URL
        self.tenant_name = settings.WORKDAY_TENANT_NAME
        self._access_token: Optional[str] = None
        self.timeout = httpx.Timeout(30.0)

    async def _get_access_token(self) -> str:
        """Obtain OAuth2 access token from Workday."""
        if self._access_token:
            return self._access_token

        token_url = f"{self.tenant_url}/oauth2/{self.tenant_name}/token"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": settings.WORKDAY_REFRESH_TOKEN.get_secret_value(),
                    "client_id": settings.WORKDAY_CLIENT_ID,
                    "client_secret": settings.WORKDAY_CLIENT_SECRET.get_secret_value(),
                },
            )
            response.raise_for_status()
            self._access_token = response.json()["access_token"]
            return self._access_token

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_integration_events(
        self, integration_name: Optional[str] = None, status: str = "Failed"
    ) -> List[Dict[str, Any]]:
        """
        Fetch integration events/logs from Workday.
        Returns list of integration run events.
        """
        try:
            token = await self._get_access_token()
            url = f"{self.tenant_url}/v1/{self.tenant_name}/integrations/events"
            params = {"status": status}
            if integration_name:
                params["integrationName"] = integration_name

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
                response.raise_for_status()
                return response.json().get("data", [])
        except Exception as e:
            logger.warning(f"Could not fetch Workday integration events: {str(e)}")
            return []

    async def validate_integration_config(
        self, integration_name: str
    ) -> Dict[str, Any]:
        """Validate an integration's configuration status."""
        # Placeholder: In production, query Workday Integration System details
        return {
            "integration_name": integration_name,
            "status": "active",
            "last_successful_run": None,
            "configuration_valid": True,
        }

    def classify_error(self, error_text: str) -> Dict[str, Any]:
        """
        Classify an error message into known Workday error categories.
        Returns category, confidence, and likely resolution path.
        """
        error_lower = error_text.lower()
        matches = {}

        for category, patterns in ERROR_PATTERNS.items():
            score = sum(1 for p in patterns if p in error_lower)
            if score > 0:
                matches[category] = score

        if not matches:
            return {
                "category": "unknown",
                "confidence": 0.3,
                "description": "Unclassified error - requires manual investigation",
            }

        best_category = max(matches, key=matches.get)
        confidence = min(0.95, 0.5 + (matches[best_category] * 0.15))

        descriptions = {
            "authentication_failure": "Workday authentication/credentials issue",
            "data_validation_error": "Data format or validation failure",
            "connectivity_issue": "Network or endpoint connectivity problem",
            "business_process_error": "Workday business process or security configuration",
            "data_mapping_error": "Integration field mapping or transformation failure",
            "integration_system_error": "Integration system user or launch configuration",
        }

        return {
            "category": best_category,
            "confidence": confidence,
            "description": descriptions.get(best_category, "Unknown"),
            "all_matches": matches,
        }

    def extract_integration_context(self, text: str) -> Dict[str, Any]:
        """Extract Workday integration context from ticket text."""
        text_lower = text.lower()
        found_integrations = [
            i for i in COMMON_WORKDAY_INTEGRATIONS if i.lower() in text_lower
        ]

        error_classification = self.classify_error(text)

        return {
            "likely_integrations": found_integrations,
            "error_classification": error_classification,
            "has_workday_context": bool(found_integrations or error_classification["category"] != "unknown"),
        }

    async def health_check(self) -> bool:
        try:
            if not self.tenant_url:
                return False
            await self._get_access_token()
            return True
        except Exception:
            return False
