  (function () {
    const canvas = document.getElementById('three-canvas');
    if (!canvas || !window.THREE) return;

    const isMobile = window.innerWidth < 768;
    const DPR = Math.min(window.devicePixelRatio, isMobile ? 1 : 2);

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: !isMobile, alpha: true });
    renderer.setPixelRatio(DPR);
    renderer.setClearColor(0x000000, 0);

    const W = () => canvas.clientWidth;
    const H = () => canvas.clientHeight;
    renderer.setSize(W(), H(), false);

    const scene  = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, W() / H(), 0.1, 100);
    camera.position.z = isMobile ? 8 : 6;

    /* ── Iridescent shader ─────────────────────────────────── */
    const vert = `
      varying vec3 vNormal;
      varying vec3 vWorldPos;
      void main() {
        vNormal   = normalize(normalMatrix * normal);
        vWorldPos = (modelMatrix * vec4(position, 1.0)).xyz;
        gl_Position = projectionMatrix * viewMatrix * vec4(vWorldPos, 1.0);
      }`;

    const frag = `
      uniform float uTime;
      varying vec3 vNormal;
      varying vec3 vWorldPos;
      vec3 hue(float h) {
        return clamp(abs(mod(h*6.0+vec3(0,4,2),6.0)-3.0)-1.0, 0.0, 1.0);
      }
      void main() {
        vec3 view    = normalize(cameraPosition - vWorldPos);
        float fresnel = pow(1.0 - abs(dot(vNormal, view)), 1.9);
        float shift   = fresnel * 0.65 + uTime * 0.055
                      + vWorldPos.y * 0.07 + vWorldPos.x * 0.04;
        vec3 col     = mix(vec3(0.12,0.06,0.03), hue(fract(shift)),
                           fresnel * 0.82 + 0.18);
        gl_FragColor = vec4(col, 0.82 + fresnel * 0.15);
      }`;

    const geo  = new THREE.TorusKnotGeometry(1.25, 0.4, isMobile ? 128 : 280, 28, 2, 3);
    const mat  = new THREE.ShaderMaterial({
      vertexShader: vert, fragmentShader: frag,
      uniforms:     { uTime: { value: 0 } },
      transparent:  true,
      side:         THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geo, mat);
    scene.add(mesh);

    /* ── Particles ─────────────────────────────────────────── */
    if (!isMobile) {
      const pGeo  = new THREE.BufferGeometry();
      const count = 320;
      const pos   = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        pos[i*3]   = (Math.random() - 0.5) * 12;
        pos[i*3+1] = (Math.random() - 0.5) * 12;
        pos[i*3+2] = (Math.random() - 0.5) * 12;
      }
      pGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      const pMat = new THREE.PointsMaterial({
        color: 0xb5451b, size: 0.022, transparent: true, opacity: 0.5,
      });
      const pts = new THREE.Points(pGeo, pMat);
      scene.add(pts);
      mesh.userData.pts = pts;
    }

    /* ── Mouse parallax ────────────────────────────────────── */
    let tx = 0, ty = 0, mx = 0, my = 0;
    if (!isMobile) {
      document.addEventListener('mousemove', e => {
        mx = (e.clientX / innerWidth  - 0.5) * 2;
        my = -(e.clientY / innerHeight - 0.5) * 2;
      });
    }

    /* ── Resize ─────────────────────────────────────────────── */
    window.addEventListener('resize', () => {
      camera.aspect = W() / H();
      camera.updateProjectionMatrix();
      renderer.setSize(W(), H(), false);
    });

    /* ── Loop ──────────────────────────────────────────────── */
    let t = 0;
    (function loop() {
      requestAnimationFrame(loop);
      t += 0.011;
      mat.uniforms.uTime.value = t;
      mesh.rotation.x = t * 0.14;
      mesh.rotation.y = t * 0.21;
      mesh.rotation.z = t * 0.06;
      if (mesh.userData.pts) {
        mesh.userData.pts.rotation.y = t * 0.035;
        mesh.userData.pts.rotation.x = t * 0.018;
      }
      tx += (mx * 0.45 - tx) * 0.045;
      ty += (my * 0.28 - ty) * 0.045;
      camera.position.x = tx;
      camera.position.y = ty;
      camera.lookAt(0, 0, 0);
      renderer.render(scene, camera);
    })();
  })();

  (function () {
    if (!window.gsap) return;
    gsap.registerPlugin(ScrollTrigger);

    /* Hero — staggered entrance */
    const heroItems = ['.hero-eyebrow', '.hero-headline', '.hero-sub', '.hero-actions'];
    gsap.set(heroItems, { opacity: 0, y: 22 });
    gsap.to(heroItems, {
      opacity:  1, y: 0,
      duration: 0.95,
      stagger:  0.16,
      ease:     'power3.out',
      delay:    0.25,
    });

    /* Section titles */
    gsap.set(['.section-eye', '.section-title'], { opacity: 0, y: 28 });
    gsap.to(['.section-eye', '.section-title'], {
      opacity:  1, y: 0,
      duration: 0.85,
      stagger:  0.12,
      ease:     'power3.out',
      scrollTrigger: { trigger: '#features', start: 'top 72%' },
    });

    /* Bento cards */
    gsap.set('.bento-card', { opacity: 0, y: 36, scale: 0.97 });
    gsap.to('.bento-card', {
      opacity:  1, y: 0, scale: 1,
      duration: 0.75,
      stagger:  0.08,
      ease:     'power3.out',
      scrollTrigger: { trigger: '.bento', start: 'top 76%' },
    });

    /* Final CTA */
    gsap.set(['.cta-title', '.cta-sub', '.cta-section .mag'], { opacity: 0, y: 28 });
    gsap.to(['.cta-title', '.cta-sub', '.cta-section .mag'], {
      opacity:  1, y: 0,
      duration: 0.85,
      stagger:  0.14,
      ease:     'power3.out',
      scrollTrigger: { trigger: '.cta-section', start: 'top 76%' },
    });

    /* About — mission + team cards */
    gsap.set(['.mission'], { opacity: 0, y: 24 });
    gsap.to('.mission', {
      opacity:  1, y: 0,
      duration: 0.8,
      ease:     'power3.out',
      scrollTrigger: { trigger: '#about', start: 'top 74%' },
    });

    gsap.set('.team-card', { opacity: 0, y: 32, scale: 0.96 });
    gsap.to('.team-card', {
      opacity:  1, y: 0, scale: 1,
      duration: 0.7,
      stagger:  0.1,
      ease:     'power3.out',
      scrollTrigger: { trigger: '.team-grid', start: 'top 78%' },
    });
  })();

  (function () {
    const THEMES = ['auto', 'light', 'dark'];
    const ICONS  = { auto: '◐', light: '○', dark: '●' };
    const btn    = document.getElementById('theme-btn');
    if (!btn) return;

    function apply(t) {
      document.documentElement.setAttribute('data-theme', t);
      btn.textContent = ICONS[t];
      localStorage.setItem('bakix-theme', t);
    }

    const saved = localStorage.getItem('bakix-theme') || 'auto';
    apply(saved);

    btn.addEventListener('click', () => {
      const cur  = localStorage.getItem('bakix-theme') || 'auto';
      const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
      apply(next);
    });
  })();

  (function () {
    document.querySelectorAll('.mag').forEach(wrap => {
      const inner = wrap.children[0];
      if (!inner) return;

      wrap.addEventListener('mousemove', e => {
        const r  = wrap.getBoundingClientRect();
        const dx = (e.clientX - r.left - r.width  / 2) * 0.32;
        const dy = (e.clientY - r.top  - r.height / 2) * 0.32;
        if (window.gsap) {
          gsap.to(wrap, { x: dx, y: dy, duration: 0.38, ease: 'power3.out' });
        } else {
          wrap.style.transform = `translate(${dx}px,${dy}px)`;
        }
      });

      wrap.addEventListener('mouseleave', () => {
        if (window.gsap) {
          gsap.to(wrap, { x: 0, y: 0, duration: 0.7, ease: 'elastic.out(1,0.55)' });
        } else {
          wrap.style.transform = 'translate(0,0)';
        }
      });
    });
  })();