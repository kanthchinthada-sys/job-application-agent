import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding, withHashLocation } from '@angular/router';

import { routes } from './app.routes';
import { authInterceptor } from './auth.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    // Hash URLs (/ui/#/jobs) so a page refresh never collides with the backend's /jobs API.
    provideRouter(routes, withHashLocation(), withComponentInputBinding()),  // route :id -> component input
    provideHttpClient(withInterceptors([authInterceptor])),
  ],
};
