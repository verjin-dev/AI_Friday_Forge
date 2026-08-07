import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Root from "./Root.jsx";
import "leaflet/dist/leaflet.css";
import "./index.css";
import "./App.css";
import "./styles/login.css";
import "./styles/vehicle.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Root />
  </StrictMode>
);
