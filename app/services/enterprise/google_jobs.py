import json

from google.oauth2 import service_account
from loguru import logger

from app.core.settings import settings


class GoogleJobsService:
    def __init__(self):
        self.scopes = ["https://www.googleapis.com/auth/indexing"]
        self.endpoint = "https://indexing.googleapis.com/v3/urlNotifications:publish"
        self._credentials = None

    def _get_credentials(self):
        if not settings.google_service_account_json:
            logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON not configured. Google Jobs indexing disabled.")
            return None

        try:
            service_account_info = json.loads(settings.google_service_account_json)
            return service_account.Credentials.from_service_account_info(
                service_account_info, scopes=self.scopes
            )
        except Exception as e:
            logger.error(f"Failed to load Google Service Account: {e}")
            return None

    async def notify_job_update(self, job_url: str, update_type: str = "URL_UPDATED"):
        """
        Notify Google that a job URL has been updated or deleted.
        update_type can be 'URL_UPDATED' or 'URL_DELETED'.
        """
        credentials = self._get_credentials()
        if not credentials:
            return False

        try:
            import httplib2

            _http = credentials.authorize(httplib2.Http())

            body = {"url": job_url, "type": update_type}

            # Using raw http request because Indexing API is simple but often tricky with discovery
            import httpx

            # Use Authlib or Google Auth to get token
            from google.auth.transport.requests import Request

            credentials.refresh(Request())
            token = credentials.token

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.endpoint, json=body, headers={"Authorization": f"Bearer {token}"}
                )

            if response.status_code == 200:
                logger.info(f"Successfully notified Google of {update_type} for {job_url}")
                return True
            logger.error(f"Failed to notify Google: {response.status_code} - {response.text}")
            return False

        except Exception as e:
            logger.error(f"Error notifying Google Indexing API: {e}")
            return False


google_jobs_service = GoogleJobsService()
