import { Link } from 'react-router';

export default function NotFound() {
  return (
    <div className="page">
      <h1 className="m-0">Not found</h1>
      <p className="page__lede">There is no page here. <Link to="/">Go to the start</Link>.</p>
    </div>
  );
}
