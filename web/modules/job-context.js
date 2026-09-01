import { buildJobContextText } from "./helpers.js";

const jobContextState = { activeJobContext: null };

function setActiveJobContext(context) {
  jobContextState.activeJobContext = context;
}

function publicJobContext(job) {
  return {
    id: String(job.id),
    source: "public",
    title: job.title,
    company: job.company_name,
    location: job.location?.display_name,
    workMode: job.work_mode,
    jd: buildJobContextText({
      title: job.title,
      company: job.company_name,
      location: job.location?.display_name,
      workMode: job.work_mode,
      recruitingTerm: job.recruiting_term?.display_name,
      description: job.description,
    }),
  };
}

function waterlooWorksJobContext(job) {
  return {
    id: String(job.source_job_id),
    source: "waterloo_work",
    title: job.title,
    company: job.organization,
    location: job.location_text,
    workMode: job.work_mode,
    applicationDeadline: job.submitted_application_deadline || job.application_deadline,
    jd: buildJobContextText({
      title: job.title,
      company: job.organization,
      location: job.location_text,
      workMode: job.work_mode,
      sourceJobId: job.source_job_id,
      applicationDeadline: job.submitted_application_deadline || job.application_deadline,
      description: job.description,
    }),
  };
}

export {
  jobContextState,
  publicJobContext,
  setActiveJobContext,
  waterlooWorksJobContext,
};
