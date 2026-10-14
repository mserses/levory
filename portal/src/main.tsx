import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { handleSilentCallback } from './auth/silent';
import { config } from './config/env';
import { applyTheme, readTheme } from './lib/preferences';
import './styles/global.css';

applyTheme(readTheme());

async function start() {
  // OIDC sessiz yenileme iframe'i: uygulamayı kurmadan yanıtı üst pencereye ilet
  if (await handleSilentCallback(window.location.pathname, config)) return;

  const root = document.getElementById('root');
  if (!root) throw new Error('#root bulunamadı');
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void start();
