import { patients, studies, studyHistory } from '../../src/thorax-web/lib/server/fixtures.ts';

console.log(JSON.stringify({
  patients,
  studies,
  history: studies.flatMap((study) => studyHistory(study).map((report) => ({
    ...report,
    patient_id: study.patient_id,
  }))),
}));
