import LoginForm from "../components/login/LoginForm.jsx";
import LoginVisual from "../components/login/LoginVisual.jsx";

export default function LoginPage({ onSignIn }) {
  return (
    <main className="login">
      <LoginVisual />
      <LoginForm onSignIn={onSignIn} />
    </main>
  );
}
