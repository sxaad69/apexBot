"""
API Manager
Handles HTTP requests with retry logic, rate limiting, and error handling
"""

import time
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timedelta


class APIManager:
    """
    API Request Manager
    Handles HTTP communication with retry logic and rate limiting
    """
    
    def __init__(self, config, logger):
        """
        Initialize API Manager
        
        Args:
            config: Configuration object
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'ApexHunter/14.0'})
        
        # Rate limiting tracking
        self.request_counts: Dict[str, list] = {}
        self.error_counts: Dict[str, int] = {}
        self.last_error_reset = datetime.now()
        
        self.logger.system("API Manager initialized")
    
    def _check_rate_limit(self, endpoint: str) -> bool:
        """
        Check if request would exceed rate limits
        
        Args:
            endpoint: API endpoint
        
        Returns:
            True if request is allowed
        """
        now = datetime.now()
        
        # Clean old requests (older than 1 hour)
        if endpoint in self.request_counts:
            self.request_counts[endpoint] = [
                ts for ts in self.request_counts[endpoint]
                if now - ts < timedelta(hours=1)
            ]
        else:
            self.request_counts[endpoint] = []
        
        # KuCoin rate limits vary by endpoint
        # General limit: ~1200 requests per minute
        # Using conservative buffer from config
        max_requests_per_minute = int(1200 * self.config.RATE_LIMIT_BUFFER)
        
        recent_requests = [
            ts for ts in self.request_counts[endpoint]
            if now - ts < timedelta(minutes=1)
        ]
        
        if len(recent_requests) >= max_requests_per_minute:
            self.logger.warning(
                f"Rate limit would be exceeded for {endpoint}",
                recent_count=len(recent_requests),
                limit=max_requests_per_minute
            )
            return False
        
        return True
    
    def _check_error_threshold(self) -> bool:
        """
        Check if error threshold has been exceeded
        
        Returns:
            True if errors are within acceptable limits
        """
        now = datetime.now()
        
        # Reset error counts every hour
        if now - self.last_error_reset > timedelta(hours=1):
            self.error_counts = {}
            self.last_error_reset = now
        
        total_errors = sum(self.error_counts.values())
        
        if total_errors >= self.config.MAX_API_ERRORS_PER_HOUR:
            self.logger.error(
                "Maximum API errors per hour exceeded",
                total_errors=total_errors,
                threshold=self.config.MAX_API_ERRORS_PER_HOUR
            )
            return False
        
        return True
    
    def _record_request(self, endpoint: str):
        """Record a successful request for rate limiting"""
        if endpoint not in self.request_counts:
            self.request_counts[endpoint] = []
        self.request_counts[endpoint].append(datetime.now())
    
    def _record_error(self, endpoint: str):
        """Record an API error"""
        self.error_counts[endpoint] = self.error_counts.get(endpoint, 0) + 1
    
    def request(self, method: str, url: str, headers: Dict[str, str],
               body: Optional[Dict[str, Any]] = None, endpoint: str = '') -> Optional[Dict[str, Any]]:
        """
        Make an API request with retry logic
        
        Args:
            method: HTTP method (GET, POST, DELETE)
            url: Full URL
            headers: Request headers
            body: Request body (for POST)
            endpoint: Endpoint path for tracking
        
        Returns:
            Response data or None on failure
        """
        # Check error threshold
        if not self._check_error_threshold():
            self.logger.error("API error threshold exceeded, request blocked")
            return None
        
        # Check rate limit
        if not self._check_rate_limit(endpoint):
            time.sleep(1)  # Brief wait if rate limited
            if not self._check_rate_limit(endpoint):
                self.logger.error("Rate limit exceeded after wait")
                return None
        
        # Retry loop
        for attempt in range(self.config.RETRY_ATTEMPTS):
            try:
                start_time = time.time()
                
                # Make request
                if method == 'GET':
                    response = self.session.get(
                        url,
                        headers=headers,
                        timeout=self.config.API_TIMEOUT
                    )
                elif method == 'POST':
                    response = self.session.post(
                        url,
                        headers=headers,
                        json=body,
                        timeout=self.config.API_TIMEOUT
                    )
                elif method == 'DELETE':
                    response = self.session.delete(
                        url,
                        headers=headers,
                        timeout=self.config.API_TIMEOUT
                    )
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                duration = time.time() - start_time
                
                # Log the API call
                self.logger.api_call(
                    method=method,
                    url=endpoint or url,
                    status=response.status_code,
                    duration=duration
                )
                
                # Handle response
                if response.status_code == 200:
                    self._record_request(endpoint)
                    data = response.json()
                    
                    # Check KuCoin response code
                    if data.get('code') == '200000':
                        return data.get('data', {})
                    else:
                        self.logger.error(
                            f"KuCoin API error: {data.get('msg', 'Unknown error')}",
                            code=data.get('code')
                        )
                        self._record_error(endpoint)
                        return None
                
                elif response.status_code == 429:
                    # Rate limited
                    self.logger.warning("Rate limited by exchange, backing off")
                    time.sleep(self.config.RETRY_DELAY * (attempt + 1))
                    continue
                
                elif response.status_code >= 500:
                    # Server error, retry
                    self.logger.warning(
                        f"Server error {response.status_code}, retrying",
                        attempt=attempt + 1,
                        max_attempts=self.config.RETRY_ATTEMPTS
                    )
                    time.sleep(self.config.RETRY_DELAY * (attempt + 1))
                    continue
                
                else:
                    # Client error (400-499), don't retry
                    self.logger.error(
                        f"API request failed with status {response.status_code}",
                        response=response.text
                    )
                    self._record_error(endpoint)
                    return None
            
            except requests.exceptions.Timeout:
                self.logger.warning(
                    f"Request timeout (attempt {attempt + 1}/{self.config.RETRY_ATTEMPTS})"
                )
                if attempt < self.config.RETRY_ATTEMPTS - 1:
                    time.sleep(self.config.RETRY_DELAY * (attempt + 1))
                    continue
                else:
                    self._record_error(endpoint)
                    return None
            
            except requests.exceptions.ConnectionError:
                self.logger.error(
                    f"Connection error (attempt {attempt + 1}/{self.config.RETRY_ATTEMPTS})"
                )
                if attempt < self.config.RETRY_ATTEMPTS - 1:
                    time.sleep(self.config.RETRY_DELAY * (attempt + 1))
                    continue
                else:
                    self._record_error(endpoint)
                    return None
            
            except Exception as e:
                self.logger.error(
                    f"Unexpected error in API request: {str(e)}",
                    exc_info=True
                )
                self._record_error(endpoint)
                return None
        
        # All retries exhausted
        self.logger.error(f"All retry attempts exhausted for {endpoint}")
        self._record_error(endpoint)
        return None
    
    def get_error_statistics(self) -> Dict[str, Any]:
        """Get API error statistics"""
        return {
            'total_errors': sum(self.error_counts.values()),
            'errors_by_endpoint': dict(self.error_counts),
            'last_reset': self.last_error_reset.isoformat()
        }
