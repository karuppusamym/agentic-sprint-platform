import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The API's CORS policy allows http://localhost:5173 only, so the port is fixed.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
