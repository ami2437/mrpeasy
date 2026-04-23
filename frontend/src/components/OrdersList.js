import React, { useState, useEffect } from 'react';
import { getCustomerOrders } from '../services/apiService';
import './OrdersList.css';

function OrdersList() {
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchOrders();
  }, []);

  const fetchOrders = async () => {
    try {
      setLoading(true);
      const response = await getCustomerOrders();
      setOrders(response.data.data || []);
      setError(null);
    } catch (err) {
      setError('Failed to fetch orders');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div className="loading">Loading orders...</div>;
  if (error) return <div className="error">{error}</div>;

  return (
    <div className="orders-list">
      <h2>Customer Orders</h2>
      <table className="orders-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Code</th>
            <th>Customer</th>
            <th>Status</th>
            <th>Total Price</th>
            <th>Date</th>
          </tr>
        </thead>
        <tbody>
          {Array.isArray(orders) && orders.length > 0 ? (
            orders.map((order) => (
              <tr key={order.cust_ord_id}>
                <td>{order.cust_ord_id}</td>
                <td>{order.code}</td>
                <td>{order.customer_name}</td>
                <td><span className={`status-${order.status}`}>{order.status_txt}</span></td>
                <td>{order.total_price_cur || order.total_price}</td>
                <td>{new Date(order.created * 1000).toLocaleDateString()}</td>
              </tr>
            ))
          ) : (
            <tr><td colSpan="6">No orders found</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export default OrdersList;
