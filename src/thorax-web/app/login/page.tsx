import { Login } from '@/features/auth/Login';

export const metadata = { title: 'Sign in' };
export default function LoginPage() { return <Login localDemo={!process.env.THORAX_API_ORIGIN} />; }
