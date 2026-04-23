import requests
from requests.auth import HTTPBasicAuth
from app.config.settings import settings
from typing import Optional, Dict, Any


class MRPeasyAPIClient:
    """Client for MRPeasy REST API"""

    def __init__(self):
        self.base_url = settings.mrpeasy_api_base_url
        self.auth = HTTPBasicAuth(
            settings.mrpeasy_api_key,
            settings.mrpeasy_api_secret
        )
        self.headers = {"Content-Type": "application/json"}

    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[Any, Any]]:
        """Make HTTP request to MRPeasy API"""
        try:
            url = f"{self.base_url}{endpoint}"
            response = requests.request(
                method,
                url,
                auth=self.auth,
                headers=self.headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json() if response.text else None
        except requests.exceptions.RequestException as e:
            raise Exception(f"MRPeasy API Error: {str(e)}")
    
    def _paginated_request(self, method: str, endpoint: str, **kwargs) -> list:
        """Make paginated GET requests to fetch all items (handles 100/1000 item limits)"""
        all_items = []
        offset = 0
        batch_size = 1000  # Maximum allowed per request
        
        # Get params from kwargs or create empty dict
        params = kwargs.get('params', {})
        
        # Check if user specified a total limit
        user_limit = params.pop('limit', None)  # Remove from params to avoid conflict
        
        while True:
            # Set pagination parameters
            paginated_params = {**params, 'limit': batch_size, 'offset': offset}
            paginated_kwargs = {**kwargs, 'params': paginated_params}
            
            try:
                url = f"{self.base_url}{endpoint}"
                response = requests.request(
                    method,
                    url,
                    auth=self.auth,
                    headers=self.headers,
                    **paginated_kwargs
                )
                response.raise_for_status()
                
                # Parse response
                batch = response.json() if response.text else []
                
                # Handle empty response or non-list response
                if not batch or not isinstance(batch, list):
                    break
                
                all_items.extend(batch)
                
                # Check if user specified a limit and we've reached it
                if user_limit is not None and len(all_items) >= user_limit:
                    all_items = all_items[:user_limit]  # Trim to exact limit
                    break
                
                # If we got fewer items than batch_size, we've reached the end
                if len(batch) < batch_size:
                    break
                
                # Move to next batch
                offset += batch_size
                
                # Safety check: stop if we've fetched too many (prevent infinite loop)
                if offset > 50000:  # Reasonable upper limit
                    break
                    
            except requests.exceptions.RequestException as e:
                raise Exception(f"MRPeasy API Error during pagination: {str(e)}")
        
        return all_items

    def get_customer_orders(self, filters: Optional[Dict] = None) -> list:
        """Get all customer orders with automatic pagination"""
        params = filters or {}
        # Request custom fields to be included in response
        if 'show_custom' not in params:
            params['show_custom'] = 'true'
        return self._paginated_request("GET", "/customer-orders", params=params)

    def get_customer_order(self, order_id: int) -> Dict:
        """Get specific customer order"""
        return self._request("GET", f"/customer-orders/{order_id}")

    def get_stock_items(self, filters: Optional[Dict] = None) -> list:
        """Get all stock items with automatic pagination"""
        return self._paginated_request("GET", "/items", params=filters or {})

    def get_stock_item(self, item_id: int) -> Dict:
        """Get specific stock item"""
        return self._request("GET", f"/items/{item_id}")

    def get_manufacturing_orders(self, filters: Optional[Dict] = None) -> list:
        """Get all manufacturing orders with automatic pagination"""
        return self._paginated_request("GET", "/manufacturing-orders", params=filters or {})

    def get_manufacturing_order(self, order_id: int) -> Dict:
        """Get specific manufacturing order"""
        return self._request("GET", f"/manufacturing-orders/{order_id}")

    def get_vendors(self, filters: Optional[Dict] = None) -> list:
        """Get all vendors"""
        return self._request("GET", "/vendors", params=filters or {})

    def get_inventory(self, filters: Optional[Dict] = None) -> list:
        """Get inventory data"""
        return self._request("GET", "/stock/inventory", params=filters or {})

    def get_report(self, report_type: str, filters: Optional[Dict] = None) -> Dict:
        """Get specific report"""
        return self._request("GET", f"/report/{report_type}", params=filters or {})

    def get_shipments(self, filters: Optional[Dict] = None) -> list:
        """Get all shipments with automatic pagination"""
        return self._paginated_request("GET", "/shipments", params=filters or {})

    def get_shipment(self, shipment_id: int) -> Dict:
        """Get specific shipment"""
        return self._request("GET", f"/shipments/{shipment_id}")

    def get_invoices(self, filters: Optional[Dict] = None) -> list:
        """Get all sales invoices with automatic pagination"""
        return self._paginated_request("GET", "/invoices", params=filters or {})

    def get_invoice(self, invoice_id: int) -> Dict:
        """Get specific sales invoice"""
        return self._request("GET", f"/invoices/{invoice_id}")


# Global instance
mrpeasy_client = MRPeasyAPIClient()
