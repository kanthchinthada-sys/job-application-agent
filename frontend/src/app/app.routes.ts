import { Routes } from '@angular/router';

import { AddJobPage } from './pages/add-job-page';
import { FindJobsPage } from './pages/find-jobs-page';
import { ApplicationsPage } from './pages/applications-page';
import { JobsPage } from './pages/jobs-page';
import { ProfilePage } from './pages/profile-page';
import { WorkspacePage } from './pages/workspace/workspace-page';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'jobs' },
  { path: 'jobs', component: JobsPage, title: 'Saved jobs · Job Application Agent' },
  { path: 'jobs/:id', component: JobsPage, title: 'Skill match · Job Application Agent' },
  { path: 'find', component: FindJobsPage, title: 'Find jobs · Job Application Agent' },
  { path: 'add', component: AddJobPage, title: 'Add job · Job Application Agent' },
  { path: 'applications', component: ApplicationsPage, title: 'Applications · Job Application Agent' },
  { path: 'applications/:id', component: WorkspacePage, title: 'Application · Job Application Agent' },
  { path: 'profile', component: ProfilePage, title: 'Profile · Job Application Agent' },
  { path: 'welcome', component: ProfilePage, data: { setup: true }, title: 'Set up your profile · Job Application Agent' },
  { path: '**', redirectTo: 'jobs' },
];
