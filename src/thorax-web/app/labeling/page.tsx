import { LabelingQueueRoute } from '@/features/labeling/LabelingQueue';

export const metadata = { title: 'Labeling queue' };
export default function LabelingPage() { return <LabelingQueueRoute localDemo={!process.env.THORAX_API_ORIGIN} />; }
