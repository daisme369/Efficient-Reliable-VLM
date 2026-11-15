import { LabelingTaskRoute } from '@/features/labeling/LabelingTask';

export const metadata = { title: 'Label image' };
export default async function LabelingTaskPage({ params }: { params: Promise<{ task_id: string }> }) {
  const { task_id } = await params;
  return <LabelingTaskRoute taskId={Number(task_id)} localDemo={!process.env.THORAX_API_ORIGIN} />;
}
