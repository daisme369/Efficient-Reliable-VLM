import { AdminRoute } from '@/components/admin-route';

export const metadata = { title: 'Admin' };
export default function AdminPage() { return <AdminRoute localDemo={!process.env.THORAX_API_ORIGIN} />; }
