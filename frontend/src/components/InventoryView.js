import React, { useState, useEffect } from 'react';
import { getInventory } from '../services/apiService';
import './InventoryView.css';

function InventoryView() {
  const [inventory, setInventory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchInventory();
  }, []);

  const fetchInventory = async () => {
    try {
      setLoading(true);
      const response = await getInventory();
      setInventory(response.data.data || []);
      setError(null);
    } catch (err) {
      setError('Failed to fetch inventory');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div className="loading">Loading inventory...</div>;
  if (error) return <div className="error">{error}</div>;

  return (
    <div className="inventory-view">
      <h2>Inventory</h2>
      <table className="inventory-table">
        <thead>
          <tr>
            <th>Item Code</th>
            <th>Item Name</th>
            <th>In Stock</th>
            <th>Available</th>
            <th>Booked</th>
            <th>Expected</th>
          </tr>
        </thead>
        <tbody>
          {Array.isArray(inventory) && inventory.length > 0 ? (
            inventory.map((item) => (
              <tr key={item.article_id}>
                <td>{item.code}</td>
                <td>{item.title}</td>
                <td>{item.in_stock || 0}</td>
                <td>{item.available || 0}</td>
                <td>{item.booked || 0}</td>
                <td>{item.expected_total || 0}</td>
              </tr>
            ))
          ) : (
            <tr><td colSpan="6">No inventory data found</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export default InventoryView;
