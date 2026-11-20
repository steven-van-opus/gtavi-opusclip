// "Slate", the clip desk mascot: an original pixel-art clapperboard with scissors and a cash stack.
// Draws into an <svg> element as crisp pixel squares. Used by hero.html and icon.html.
// drawMascot(svg, {x, y, px})  -> draws at (x, y) with pixel size px (svg user units)
(function () {
  const C = {
    k: '#1c1030', w: '#ffffff', b: '#2a2042', p: '#ff3d8b', q: '#d4256f', h: '#ff9cc4', e: '#ffffff', d: '#1c1030',
    m: '#5a0f2e', t: '#ffffff', g: '#27b84a', l: '#a4f58f', n: '#146a2a', s: '#e3e8f2', z: '#8a93ab', y: '#ffd23d',
    o: '#ff8a3d', r: '#ff5a5a',
  };
  // body with face (24 x 14)
  const BODY = [
    'kkkkkkkkkkkkkkkkkkkkkkkk',
    'kppppppppppppppppppppppk',
    'kphhpppppppppppppppppqqk',
    'kphpkkkkppppppkkkkppppqk',
    'kpppeeeeppppppeeeepppqqk',
    'kpppeeddppppppeeddpppqqk',
    'kpppeeddppppppeeddpppqqk',
    'kpppppppppppppppppppppqk',
    'kpppppmmmmmmmmmmmppppqqk',
    'kpppppmttttttttttmpppqqk',
    'kppppppmmmmmmmmmmppppqqk',
    'kppppppppppppppppppppqqk',
    'kqqqqqqqqqqqqqqqqqqqqqqk',
    'kkkkkkkkkkkkkkkkkkkkkkkk',
  ];
  // the fixed lower clapper bar (24 x 4)
  const BAR = [
    'kkkkkkkkkkkkkkkkkkkkkkkk',
    'kbwwwbbbwwwbbbwwwbbbwwwk',
    'kbbwwwbbbwwwbbbwwwbbbwwk',
    'kkkkkkkkkkkkkkkkkkkkkkkk',
  ];
  // the hinged top stick (24 x 4), drawn rotated open
  const STICK = [
    'kkkkkkkkkkkkkkkkkkkkkkkk',
    'kwwwbbbwwwbbbwwwbbbwwwbk',
    'kwwbbbwwwbbbwwwbbbwwwbbk',
    'kkkkkkkkkkkkkkkkkkkkkkkk',
  ];
  // cash stack held in the right hand (9 x 8)
  const CASH = [
    '.kkkkkkk.',
    'kglllllgk',
    'kgllgllgk',
    'kgnnnnngk',
    'kglllllgk',
    'kgllgllgk',
    'kgnnnnngk',
    '.kkkkkkk.',
  ];
  // scissors held in the left hand (9 x 9)
  const SCISSORS = [
    's.......s',
    'zs.....sz',
    '.zs...sz.',
    '..zs.sz..',
    '...zkz...',
    '.rr.k.rr.',
    'r..r.r..r',
    'r..r.r..r',
    '.rr...rr.',
  ];
  function rects(svg, map, ox, oy, px, group) {
    const ns = 'http://www.w3.org/2000/svg';
    const g = group || svg;
    map.forEach((row, j) => {
      [...row].forEach((ch, i) => {
        if (ch === '.' || !C[ch]) return;
        const r = document.createElementNS(ns, 'rect');
        r.setAttribute('x', ox + i * px); r.setAttribute('y', oy + j * px);
        r.setAttribute('width', px + 0.6); r.setAttribute('height', px + 0.6);
        r.setAttribute('fill', C[ch]);
        g.appendChild(r);
      });
    });
  }
  function group(svg, transform) {
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    if (transform) g.setAttribute('transform', transform);
    g.setAttribute('shape-rendering', 'crispEdges');
    svg.appendChild(g);
    return g;
  }
  window.drawMascot = function (svg, opt) {
    const px = opt.px || 12, x = opt.x || 0, y = opt.y || 0;
    const root = group(svg, `translate(${x},${y})`);
    // arms (behind the body)
    rects(svg, ['kkkk', 'kppk', 'kkkk'], -3 * px, 11 * px, px, root);
    rects(svg, ['kkkk', 'kppk', 'kkkk'], 23 * px, 11 * px, px, root);
    // feet
    rects(svg, ['kkkk', 'kqqk', 'kkkk'], 4 * px, 21 * px, px, root);
    rects(svg, ['kkkk', 'kqqk', 'kkkk'], 16 * px, 21 * px, px, root);
    // body and the lower clapper bar
    rects(svg, BAR, 0, 4 * px, px, root);
    rects(svg, BODY, 0, 8 * px, px, root);
    // the open top stick, hinged at the left
    const stick = group(root, `rotate(-12 ${0} ${4 * px})`);
    rects(svg, STICK, 0, 0, px, stick);
    // hinge
    rects(svg, ['kk', 'kk'], 0, 3 * px, px, root);
    // props
    rects(svg, CASH, 25 * px, 6 * px, px, root);
    rects(svg, SCISSORS, -10 * px, 6 * px, px, root);
  };
  window.drawBill = function (svg, x, y, px, angle) {
    const g = group(svg, `translate(${x},${y}) rotate(${angle || 0})`);
    rects(svg, ['kkkkkkkkk', 'kgggggggk', 'kglllllgk', 'kgllgllgk', 'kglllllgk', 'kgggggggk', 'kkkkkkkkk'], 0, 0, px, g);
  };
  window.drawSpark = function (svg, x, y, px, big) {
    const g = group(svg, `translate(${x},${y})`);
    rects(svg, big ? ['.y.', 'yyy', '.y.'] : ['y'], 0, 0, px, g);
  };
})();
