import React from 'react';
import './Dashboard.css';
import OrdersList from '../components/OrdersList';
import InventoryView from '../components/InventoryView';

function Dashboard() {
  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <h1>Dashboard</h1>
        <p>Welcome to MRPeasy Custom Manufacturing Portal</p>
      </div>

      <div className="dashboard-stats">
        <div className="stat-card">
          <h3>Orders</h3>
          <p>Manage customer orders</p>
        </div>
        <div className="stat-card">
          <h3>Inventory</h3>
          <p>Track stock levels</p>
        </div>
        <div className="stat-card">
          <h3>Manufacturing</h3>
          <p>Monitor production</p>
        </div>
        <div className="stat-card">
          <h3>Vendors</h3>
          <p>Manage suppliers</p>
        </div>
      </div>

      <div className="dashboard-content">
        <OrdersList />
        <InventoryView />
      </div>
    </div>
  );
}

export default Dashboard;
