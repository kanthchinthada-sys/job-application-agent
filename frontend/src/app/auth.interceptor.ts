import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { InjectionToken, inject } from '@angular/core';
import { tap } from 'rxjs';

/** Where to go when the session has expired. A token, so tests can check it without leaving the page. */
export const GO_TO_LOGIN = new InjectionToken<() => void>('GO_TO_LOGIN', {
  providedIn: 'root',
  factory: () => () => window.location.assign('/login?next=/ui/'),
});

/** Any 401 (password protection on, not signed in) sends you to the sign-in page. */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const goToLogin = inject(GO_TO_LOGIN);
  return next(req).pipe(
    tap({
      error: (err: unknown) => {
        if (err instanceof HttpErrorResponse && err.status === 401 && !req.url.startsWith('/login')) goToLogin();
      },
    }),
  );
};
