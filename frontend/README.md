# MRPeasy Custom Manufacturing Portal - Frontend

React-based web portal for visualizing and manipulating MRPeasy data.

## Features

- **Dashboard**: Overview of orders, inventory, and manufacturing data
- **Customer Orders**: View and manage customer orders
- **Inventory Management**: Track stock levels in real-time
- **Manufacturing Orders**: Monitor production orders
- **Responsive Design**: Works on desktop and mobile devices
- **Data Integration**: Seamless API integration with backend

## Setup

1. **Install dependencies**:
   ```bash
   npm install
   ```

2. **Start the development server**:
   ```bash
   npm start
   ```

The app will run on `http://localhost:3000`

## Building for Production

```bash
npm run build
```

This creates an optimized production build in the `build` directory.

## Project Structure

```
frontend/
├── public/          # Static files
├── src/
│   ├── components/  # Reusable components
│   ├── pages/       # Page components
│   ├── services/    # API service
│   ├── App.js       # Main app component
│   └── index.js     # Entry point
└── package.json
```

## Customization

- **Add Components**: Create new components in `src/components/`
- **Add Pages**: Create new pages in `src/pages/`
- **API Calls**: Use functions from `src/services/apiService.js`
- **Styling**: Modify CSS files for each component
