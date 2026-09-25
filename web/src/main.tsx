import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { RoleProvider } from "./store/role";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RoleProvider>
      <App />
    </RoleProvider>
  </StrictMode>
);
