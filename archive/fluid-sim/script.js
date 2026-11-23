// Create canvas and append to body
const canvas = document.createElement('canvas');
canvas.width = 800;
canvas.height = 600;
document.body.appendChild(canvas);
const ctx = canvas.getContext('2d');

// Create controls
const viscositySlider = document.createElement('input');
viscositySlider.type = 'range';
viscositySlider.min = '0.01';
viscositySlider.max = '1';
viscositySlider.value = '0.1';
document.body.appendChild(viscositySlider);

// Grid dimensions
const resolution = 100;
const cellSize = canvas.width / resolution;

// Initialize velocity and pressure fields
let u = new Float32Array(resolution * resolution);
let v = new Float32Array(resolution * resolution);
let p = new Float32Array(resolution * resolution);

// Simulation parameters
let viscosity = 0.1;
let dt = 0.1;

// Particle system
let particles = [];
for (let i = 0; i < 1000; i++) {
    particles.push({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height
    });
}

// Main simulation loop
function simulate() {
    // Update viscosity from slider
    viscosity = parseFloat(viscositySlider.value);

    // Advection step (simplified)
    // ...

    // Diffusion step (simplified)
    // ...

    // Projection step (simplified)
    // ...

    // Render fluid
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.beginPath();
    particles.forEach(particle => {
        const index = Math.floor(particle.y / cellSize) * resolution + Math.floor(particle.x / cellSize);
        const vx = u[index];
        const vy = v[index];
        particle.x += vx * dt;
        particle.y += vy * dt;
        ctx.fillRect(particle.x, particle.y, 1, 1);
    });
    ctx.stroke();

    requestAnimationFrame(simulate);
}

// Start simulation
simulate();

// Handle mouse interaction to add sources/sinks
canvas.addEventListener('mousedown', (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const index = Math.floor(my / cellSize) * resolution + Math.floor(mx / cellSize);
    u[index] += 10;
    v[index] -= 10;
});