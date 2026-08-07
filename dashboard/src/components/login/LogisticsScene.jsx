import { Canvas, useFrame } from "@react-three/fiber";
import { Float, Line, OrbitControls, Stars } from "@react-three/drei";
import { useRef } from "react";
import * as THREE from "three";

const bluePath = [
  [-4, 0.15, 1],
  [-1.1, 0.15, 0.4],
  [1, 0.15, -0.1],
  [4, 0.15, -1.5],
];

const reroutePath = [
  [-1.1, 0.15, 0.4],
  [-0.2, 0.15, -1.6],
  [2, 0.15, -1.8],
  [4, 0.15, -1.5],
];

function pointOnPath(points, progress) {
  const scaled = progress * (points.length - 1);
  const index = Math.min(Math.floor(scaled), points.length - 2);
  const local = scaled - index;
  return new THREE.Vector3(...points[index]).lerp(
    new THREE.Vector3(...points[index + 1]),
    local
  );
}

function Truck({ onPhaseChange }) {
  const truck = useRef(null);
  const lastPhase = useRef("");

  useFrame(({ clock }) => {
    if (!truck.current) return;

    const seconds = clock.getElapsedTime() % 13;
    const phase =
      seconds < 4 ? "enroute" : seconds < 6 ? "risk" : seconds < 8 ? "rerouting" : "optimized";

    if (phase !== lastPhase.current) {
      lastPhase.current = phase;
      onPhaseChange(phase);
    }

    const point =
      seconds < 7
        ? pointOnPath(bluePath, Math.min(seconds / 7, 1))
        : pointOnPath(reroutePath, Math.min((seconds - 7) / 6, 1));

    truck.current.position.copy(point);
    truck.current.rotation.y = seconds < 7 ? -0.36 : -0.02;
  });

  return (
    <group ref={truck}>
      <mesh position={[0, 0.42, 0]} castShadow>
        <boxGeometry args={[0.72, 0.38, 0.34]} />
        <meshStandardMaterial color="#54d6ee" metalness={0.45} roughness={0.25} />
      </mesh>
      <mesh position={[-0.35, 0.2, 0]} castShadow>
        <boxGeometry args={[0.18, 0.28, 0.33]} />
        <meshStandardMaterial color="#8993ff" metalness={0.32} roughness={0.3} />
      </mesh>
      {[-0.28, 0.28].map((x) => (
        <group key={x}>
          {[-0.2, 0.2].map((z) => (
            <mesh key={z} position={[x, 0.1, z]} rotation={[Math.PI / 2, 0, 0]}>
              <cylinderGeometry args={[0.11, 0.11, 0.07, 16]} />
              <meshStandardMaterial color="#071018" />
            </mesh>
          ))}
        </group>
      ))}
    </group>
  );
}

function Warehouse({ position, color = "#283a54" }) {
  return (
    <group position={position}>
      <mesh castShadow receiveShadow position={[0, 0.38, 0]}>
        <boxGeometry args={[1.25, 0.76, 0.9]} />
        <meshStandardMaterial color={color} metalness={0.25} roughness={0.52} />
      </mesh>
      <mesh position={[0, 0.84, 0]}>
        <coneGeometry args={[0.84, 0.38, 4]} rotation={[0, Math.PI / 4, 0]} />
        <meshStandardMaterial color="#496483" />
      </mesh>
      <mesh position={[0, 0.45, 0.46]}>
        <planeGeometry args={[0.68, 0.22]} />
        <meshBasicMaterial color="#69e0ee" />
      </mesh>
    </group>
  );
}

function Package({ position }) {
  return (
    <mesh position={position} castShadow>
      <boxGeometry args={[0.25, 0.25, 0.25]} />
      <meshStandardMaterial color="#f6b95f" emissive="#553410" emissiveIntensity={0.3} />
    </mesh>
  );
}

function NetworkNode({ position, color }) {
  return (
    <Float speed={2} rotationIntensity={0.4} floatIntensity={0.5}>
      <mesh position={position}>
        <sphereGeometry args={[0.09, 16, 16]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.2} />
      </mesh>
    </Float>
  );
}

function Scene({ onPhaseChange }) {
  return (
    <>
      <color attach="background" args={["#0b1322"]} />
      <fog attach="fog" args={["#0b1322", 7, 17]} />
      <ambientLight intensity={0.75} />
      <directionalLight castShadow position={[4, 7, 3]} intensity={2.1} color="#d9edff" />
      <pointLight position={[-4, 2, 1]} color="#737eff" intensity={9} distance={8} />
      <pointLight position={[3, 2, -2]} color="#55dce7" intensity={7} distance={7} />

      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[18, 18]} />
        <meshStandardMaterial color="#101e2c" roughness={0.72} metalness={0.15} />
      </mesh>

      <Line points={bluePath} color="#4acdea" lineWidth={3} transparent opacity={0.76} />
      <Line
        points={reroutePath}
        color="#7c8cff"
        lineWidth={3}
        transparent
        opacity={0.88}
        dashed
        dashSize={0.24}
        gapSize={0.12}
      />
      <Line
        points={[
          [-4, 0.05, 1],
          [4, 0.05, -1.5],
        ]}
        color="#f05f73"
        lineWidth={2}
        transparent
        opacity={0.76}
        dashed
        dashSize={0.18}
        gapSize={0.12}
      />

      <Warehouse position={[-4, 0, 1]} />
      <Warehouse position={[-0.85, 0, 0.35]} color="#254a5a" />

      <group position={[4, 0, -1.5]}>
        <mesh position={[0, 0.65, 0]}>
          <cylinderGeometry args={[0.18, 0.18, 1.25, 18]} />
          <meshStandardMaterial color="#506d8b" />
        </mesh>
        <mesh position={[0, 1.35, 0]}>
          <coneGeometry args={[0.42, 0.55, 4]} />
          <meshStandardMaterial color="#59d8e9" emissive="#1c6379" emissiveIntensity={0.7} />
        </mesh>
      </group>

      <Package position={[-3.15, 0.13, 1.15]} />
      <Package position={[-3.45, 0.13, 0.75]} />
      <Package position={[-0.3, 0.13, 0.64]} />

      <group position={[1.05, 0.15, -0.12]}>
        <mesh>
          <sphereGeometry args={[0.15, 20, 20]} />
          <meshStandardMaterial color="#f06174" emissive="#f06174" emissiveIntensity={1.4} />
        </mesh>
        <mesh position={[0, 0.35, 0]}>
          <coneGeometry args={[0.12, 0.35, 16]} />
          <meshStandardMaterial color="#f06174" emissive="#f06174" emissiveIntensity={0.8} />
        </mesh>
      </group>

      {[
        [-2.8, 0.55, -1.2],
        [-1.75, 0.8, 2.1],
        [0.7, 0.55, 1.3],
        [2.7, 0.8, 0.5],
        [3.2, 0.5, -2.8],
      ].map((position, index) => (
        <NetworkNode
          key={`${position[0]}-${position[2]}`}
          position={position}
          color={index % 2 ? "#7787ff" : "#58ddeb"}
        />
      ))}

      <Truck onPhaseChange={onPhaseChange} />
      <Stars radius={18} depth={8} count={100} factor={1.5} saturation={0} fade speed={0.5} />
      <OrbitControls
        enablePan={false}
        enableZoom={false}
        minPolarAngle={0.85}
        maxPolarAngle={1.15}
        autoRotate
        autoRotateSpeed={0.25}
      />
    </>
  );
}

export default function LogisticsScene({ onPhaseChange }) {
  return (
    <Canvas
      className="logistics-canvas"
      shadows
      dpr={[1, 1.5]}
      gl={{ preserveDrawingBuffer: true }}
      camera={{ position: [0, 7, 9], fov: 45 }}
    >
      <Scene onPhaseChange={onPhaseChange} />
    </Canvas>
  );
}
