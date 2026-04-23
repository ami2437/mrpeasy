const axios = require('axios');
const config = require('../config');

// Create axios instance with Basic Auth
const apiClient = axios.create({
  baseURL: config.mrpeasy.baseUrl,
  auth: {
    username: config.mrpeasy.apiKey,
    password: config.mrpeasy.apiSecret,
  },
  headers: {
    'Content-Type': 'application/json',
  },
});

class MRPeasyService {
  /**
   * Fetch all customer orders with optional filters
   */
  async getCustomerOrders(filters = {}) {
    try {
      const response = await apiClient.get('/customer-orders', { params: filters });
      return response.data;
    } catch (error) {
      throw new Error(`Failed to fetch customer orders: ${error.message}`);
    }
  }

  /**
   * Fetch a single customer order by ID
   */
  async getCustomerOrderById(id) {
    try {
      const response = await apiClient.get(`/customer-orders/${id}`);
      return response.data;
    } catch (error) {
      throw new Error(`Failed to fetch customer order ${id}: ${error.message}`);
    }
  }

  /**
   * Create a new customer order
   */
  async createCustomerOrder(orderData) {
    try {
      const response = await apiClient.post('/customer-orders', orderData);
      return response.data;
    } catch (error) {
      throw new Error(`Failed to create customer order: ${error.message}`);
    }
  }

  /**
   * Update a customer order
   */
  async updateCustomerOrder(id, updateData) {
    try {
      const response = await apiClient.put(`/customer-orders/${id}`, updateData);
      return response.data;
    } catch (error) {
      throw new Error(`Failed to update customer order ${id}: ${error.message}`);
    }
  }

  /**
   * Fetch all stock items
   */
  async getStockItems(filters = {}) {
    try {
      const response = await apiClient.get('/items', { params: filters });
      return response.data;
    } catch (error) {
      throw new Error(`Failed to fetch stock items: ${error.message}`);
    }
  }

  /**
   * Fetch a single stock item by ID
   */
  async getStockItemById(id) {
    try {
      const response = await apiClient.get(`/items/${id}`);
      return response.data;
    } catch (error) {
      throw new Error(`Failed to fetch stock item ${id}: ${error.message}`);
    }
  }

  /**
   * Fetch all manufacturing orders
   */
  async getManufacturingOrders(filters = {}) {
    try {
      const response = await apiClient.get('/manufacturing-orders', { params: filters });
      return response.data;
    } catch (error) {
      throw new Error(`Failed to fetch manufacturing orders: ${error.message}`);
    }
  }

  /**
   * Fetch a single manufacturing order by ID
   */
  async getManufacturingOrderById(id) {
    try {
      const response = await apiClient.get(`/manufacturing-orders/${id}`);
      return response.data;
    } catch (error) {
      throw new Error(`Failed to fetch manufacturing order ${id}: ${error.message}`);
    }
  }

  /**
   * Fetch all vendors
   */
  async getVendors(filters = {}) {
    try {
      const response = await apiClient.get('/vendors', { params: filters });
      return response.data;
    } catch (error) {
      throw new Error(`Failed to fetch vendors: ${error.message}`);
    }
  }

  /**
   * Fetch inventory data
   */
  async getInventory(filters = {}) {
    try {
      const response = await apiClient.get('/stock/inventory', { params: filters });
      return response.data;
    } catch (error) {
      throw new Error(`Failed to fetch inventory: ${error.message}`);
    }
  }

  /**
   * Fetch report data
   */
  async getReport(reportType, filters = {}) {
    try {
      const response = await apiClient.get(`/report/${reportType}`, { params: filters });
      return response.data;
    } catch (error) {
      throw new Error(`Failed to fetch report ${reportType}: ${error.message}`);
    }
  }
}

module.exports = new MRPeasyService();
