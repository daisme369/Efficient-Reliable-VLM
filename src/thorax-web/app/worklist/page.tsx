import { Worklist } from '@/features/worklist/Worklist';

export const metadata = { title: 'Worklist' };
export default function WorklistPage() { return <Worklist localDemo={!process.env.THORAX_API_ORIGIN} />; }
