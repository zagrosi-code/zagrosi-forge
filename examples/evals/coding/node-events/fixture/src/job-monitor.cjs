'use strict';

// Shared by the command-line dashboard and its audit adapter.
function observeJobs(bus, showStatus, recordAudit) {
  const stopStatus = bus.subscribe('job:changed', job => showStatus(job.id, job.status));
  const stopAudit = bus.subscribeAll((job, topic) => recordAudit({ topic, id: job.id }));
  return () => [stopStatus(), stopAudit()];
}

module.exports = { observeJobs };
