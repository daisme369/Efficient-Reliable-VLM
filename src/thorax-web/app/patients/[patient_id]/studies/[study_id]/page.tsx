import { StudyRoute } from '@/features/workspace/StudyWorkspace';

export const metadata = { title: 'Study workspace' };

export default async function StudyPage({ params }: { params: Promise<{ patient_id: string; study_id: string }> }) {
  const { patient_id, study_id } = await params;
  return <StudyRoute patientId={Number(patient_id)} studyId={Number(study_id)} />;
}
