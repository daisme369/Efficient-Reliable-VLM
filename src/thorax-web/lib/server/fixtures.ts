import { createHash } from 'node:crypto';
import type { HistoryReport, ImageDTO, PatientIdentity, View } from '../contracts';

export const fixtureKey = 'thorax-demo-v1';

export const imageAssets: Record<View, Pick<ImageDTO, 'width' | 'height' | 'sha256'>> = {
  PA: { width: 800, height: 880, sha256: '95f7a5c42b6dcc1c16ff975c60de27ad6d5870b205543ef78a6d025337c43332' },
  AP: { width: 800, height: 880, sha256: '1bb63e0e941b68a233524f0dde05a7f0994c8e9a44f2352e6cfe9f9a56c84c45' },
  LAT: { width: 800, height: 880, sha256: 'c003f5a4c88f693e24b94f2ce586ae5aa81a1e9303894dd774df906388182027' },
};

const identities = [
  ['Eleanor Brooks', 1958, 'Female'],
  ['James Whitfield', 1967, 'Male'],
  ['Marina Chen', 1972, 'Female'],
  ['Daniel Morgan', 1953, 'Male'],
  ['Avery Patel', 1983, 'Female'],
  ['Julian Rivera', 1977, 'Male'],
  ['Nora Bennett', 1962, 'Female'],
  ['Theo Wilson', 1991, 'Male'],
  ['Amara Okafor', 1974, 'Female'],
  ['Liam Foster', 1985, 'Male'],
  ['Sofia Reyes', 1969, 'Female'],
  ['Miles Hart', 1957, 'Male'],
] as const;

export const patients: PatientIdentity[] = identities.map(([name, birth_year, sex], i) => ({
  id: i + 1,
  code: `PX-${String(1042 + i).padStart(4, '0')}`,
  name,
  birth_year,
  sex,
}));

const indications = [
  'Persistent cough and shortness of breath',
  'Follow-up chest radiograph',
  'Chest discomfort',
  'Dyspnea on exertion',
  'Persistent cough',
  'Interval review',
];

export interface FixtureStudy {
  id: number;
  patient_id: number;
  study_date: string;
  indication: string;
  images: ImageDTO[];
}

function makeStudy(patient_id: number, current: boolean): FixtureStudy {
  const id = 2400 + patient_id * 10 + Number(current);
  const views: View[] = current && patient_id % 3 === 1 ? ['PA', 'LAT'] : [patient_id % 3 === 0 ? 'AP' : 'PA'];
  return {
    id,
    patient_id,
    study_date: current ? `2026-11-${String(7 - (patient_id % 7)).padStart(2, '0')}` : '2026-08-18',
    indication: current ? indications[(patient_id - 1) % indications.length] : 'Prior chest examination',
    images: views.map((view, index) => {
      const imageId = id * 10 + index;
      return {
        id: imageId,
        study_id: id,
        view,
        ...imageAssets[view],
        media_type: 'image/png',
        content_url: `/api/v1/images/${imageId}/content`,
      };
    }),
  };
}

export const studies = patients.flatMap(({ id }) =>
  id === 2 ? [makeStudy(id, true)] : [makeStudy(id, true), makeStudy(id, false)],
);

export function studyHistory(study: FixtureStudy): HistoryReport[] {
  if (study.patient_id === 2) return [];
  const months = study.patient_id === 1 ? [10, 8, 7, 6, 5, 4, 3] : [8, 5];
  const eligible = months.map((month, i) => ({
    id: study.patient_id * 100 + i + 1,
    report_date: `2026-${String(month).padStart(2, '0')}-02`,
    text: i === 0
      ? 'Synthetic prior report. Mild bibasilar linear markings were described.'
      : 'Synthetic prior report. No acute cardiopulmonary finding was described.',
  }));
  return eligible.filter((report) => report.report_date < study.study_date).slice(0, 5);
}

export const fixtureChecksum = createHash('sha256')
  .update(JSON.stringify({ patients, studies }))
  .digest('hex');

export const modelArtifacts = ['mock-vlm', 'mock-cv'].map((id) => ({
  id,
  version: 'fixture-v1',
  sha256: createHash('sha256').update(`${id}:fixture-v1`).digest('hex'),
}));
