import type { NextPage } from 'next';
import LaunchScreen from '../components/launch/LaunchScreen';

/** 应用开机页：先让用户选择继续 / 新建，再进入 /studio 工作台。 */
const HomePage: NextPage = () => <LaunchScreen />;

export default HomePage;
